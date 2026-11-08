"""Synthetic prices and identities; not real market offers or sporting results."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json

import pytest

from research.polymarket_odds import PolymarketOdds, american_odds, book_quote, matching_event

NOW = datetime(2026, 11, 5, 12, tzinfo=timezone.utc)
GAME = {'game_id':'calendar-fixture','away':'LAR','home':'WSH',
        'kickoff':'2026-11-08T18:00:00Z','kickoff_confirmed':True}


@pytest.mark.parametrize('price,expected',[('.5','+100'),('.25','+300'),('.75','-300'),
    ('.8','-400'),('.2','+400'),('.6','-150'),('.4','+150'),('.52','-108'),('.001','+99900')])
def test_dollar_payout_prices_have_correct_american_sign_and_scale(price,expected):
    assert american_odds(price)==expected


@pytest.mark.parametrize('price',[0,1,-1,2,'nan','Infinity',None,True,''])
def test_nonprices_are_not_odds(price):
    with pytest.raises(ValueError):american_odds(price)


def book(token='11'):
    return {'asset_id':token,'market':'condition','timestamp':str(int(NOW.timestamp()*1000)),
            'asks':[{'price':'.9','size':'10'},{'price':'.6','size':'7'},{'price':'.6','size':'3'}],
            'bids':[{'price':'.1','size':'10'},{'price':'.59','size':'4'}]}


def event():
    return {'id':'event','slug':'nfl-la-was-2026-11-08','startTime':GAME['kickoff'],
            'active':True,'closed':False,
            'teams':[{'league':'nfl','ordering':'away','abbreviation':'la'},
                     {'league':'nfl','ordering':'home','abbreviation':'was'}],
            'markets':[{'id':'market','conditionId':'condition','question':'Fixture LA vs WAS',
                        'active':True,'closed':False,'acceptingOrders':True,'enableOrderBook':True,
                        'sportsMarketType':'moneyline','outcomes':'["LA","WAS"]',
                        'clobTokenIds':'["11","22"]','liquidity':'100',
                        'outcomePrices':'["0.99","0.01"]'}]}


def test_best_ask_not_array_order_midpoint_last_trade_or_gamma_probability():
    result=book_quote(book(),'11','condition',NOW)
    assert result['american']=='-150' and result['share_price']=='0.6'
    assert result['ask_size_shares']=='10' and result['best_bid']=='0.59'
    assert result['status']=='AVAILABLE' and result['fees_included'] is False


def test_wrong_token_condition_crossed_and_future_books_are_rejected():
    for bad in [dict(book(),asset_id='99'),dict(book(),market='wrong'),
                dict(book(),bids=[{'price':'.7','size':'2'}]),
                dict(book(),timestamp=str(int((NOW+timedelta(minutes=5)).timestamp()*1000)))]:
        with pytest.raises(ValueError):book_quote(bad,'11','condition',NOW)


def test_stale_and_empty_book_do_not_become_fresh_buy_quotes():
    assert book_quote(book(),'11','condition',NOW+timedelta(seconds=121))['status']=='STALE'
    empty=book_quote(dict(book(),asks=[]),'11','condition',NOW)
    assert empty['status']=='NO_ASK' and empty['american'] is None


def test_event_identity_requires_both_teams_league_orientation_and_kickoff():
    assert matching_event(event(),GAME)
    for bad in [dict(event(),startTime='2026-11-09T18:00:00Z'),dict(event(),teams=list(reversed(event()['teams']))[1:])]:
        assert not matching_event(bad,GAME)
    bad=event();bad['teams'][0]['ordering']='home';assert not matching_event(bad,GAME)
    bad=event();bad['teams'][0]['league']='nba';assert not matching_event(bad,GAME)


class Response:
    status_code=200
    def __init__(self,value):self.content=json.dumps(value).encode()
    def raise_for_status(self):pass


def test_provider_archives_exact_bytes_preserves_outcome_mapping_and_cache_age(tmp_path):
    now=[NOW];calls=[]
    def get(url,**kwargs):
        calls.append(url);assert kwargs['allow_redirects'] is False
        if '/events?' in url:return Response([event()])
        return Response(book('11' if 'token_id=11' in url else '22'))
    provider=PolymarketOdds(tmp_path,get=get,clock=lambda:now[0])
    report=provider.game_odds(GAME)
    assert report['status']=='AVAILABLE' and len(calls)==3
    assert [(o['label'],o['token_id'],o['american']) for o in report['markets'][0]['outcomes']]==[('LA','11','-150'),('WAS','22','-150')]
    assert report['parlay_quote'] is None and not report['betting_ready']
    provider.game_odds(GAME);assert len(calls)==3
    now[0]+=timedelta(seconds=121)
    report=provider.game_odds(GAME);assert report['available_outcomes']==0
    assert all(o['status']=='STALE' for o in report['markets'][0]['outcomes'])
    assert len(list(tmp_path.glob('quote-report-*.json')))==3
    import hashlib
    for source in report['sources']+[o['source'] for o in report['markets'][0]['outcomes']]:
        assert hashlib.sha256(open(source['archive_file'],'rb').read()).hexdigest()==source['sha256']


@pytest.mark.parametrize('damage',['closed','wrong-kickoff','duplicate','tokens','http-failure'])
def test_invalid_sources_produce_no_fake_quotes(damage,tmp_path):
    e=deepcopy(event())
    if damage=='closed':e['closed']=True
    if damage=='wrong-kickoff':e['startTime']='2026-12-01T18:00:00Z'
    if damage=='tokens':e['markets'][0]['clobTokenIds']='["11"]'
    def get(url,**kwargs):
        if damage=='http-failure':
            import requests
            raise requests.ConnectionError('fixture unavailable')
        return Response([e,e] if damage=='duplicate' else [e])
    report=PolymarketOdds(tmp_path,get=get,clock=lambda:NOW).game_odds(GAME)
    assert report['status']=='UNAVAILABLE' and not report.get('available_outcomes')
    assert not report['markets']


def test_filter_and_pagination_do_not_fetch_other_books():
    e=event();e['markets']*=9
    def get(url,**kwargs):return Response([e]) if '/events?' in url else Response(book('11' if 'token_id=11' in url else '22'))
    p=PolymarketOdds(get=get,clock=lambda:NOW)
    assert len(p.game_odds(GAME,page=0)['markets'])==8
    assert len(p.game_odds(GAME,page=1)['markets'])==1
    assert not p.game_odds(GAME,market_type='totals')['markets']
    with pytest.raises(ValueError):p.game_odds(GAME,page=-1)
    with pytest.raises(ValueError):p.game_odds(GAME,market_type='https://invalid')


def test_http_route_binds_quotes_to_calendar_game_and_serves_widget():
    from http.server import ThreadingHTTPServer
    from threading import Thread
    from urllib.request import urlopen
    from urllib.error import HTTPError
    from research.player_terminal import handler
    class Calendar:
        def week(self,*args):return {'games':[GAME]}
    class Provider:
        def game_odds(self,game,market_type,page):
            assert game==GAME
            return {'provider':'Polymarket','game_id':game['game_id'],'format':'AMERICAN','american':'+150'}
    server=ThreadingHTTPServer(('127.0.0.1',0),handler(None,weekly_provider=Calendar(),odds_provider=Provider()))
    thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    base=f'http://127.0.0.1:{server.server_port}'
    try:
        data=json.load(urlopen(base+'/api/polymarket/odds?game_id=calendar-fixture&market_type=moneyline'))
        assert data['american']=='+150'
        with pytest.raises(HTTPError) as error:urlopen(base+'/api/polymarket/odds?game_id=wrong')
        assert error.value.code==404
        assert b'PolymarketOdds' in urlopen(base+'/polymarket-odds.js').read()
    finally:
        server.shutdown();server.server_close();thread.join()
