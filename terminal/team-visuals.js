/* Team identity and descriptive matchup presentation; no forecast generation. */
(function (root) {
  "use strict";
  // ESPN team colors from the archived Nov 4, 2026 NFL scoreboards.
  const colors = {
    ARI:["a40227","ffffff"],ATL:["a71930","000000"],BAL:["29126f","000000"],
    BUF:["00338d","d50a0a"],CAR:["0085ca","000000"],CHI:["0b1c3a","e64100"],
    CIN:["fb4f14","000000"],CLE:["472a08","ff3c00"],DAL:["002a5c","b0b7bc"],
    DEN:["0a2343","fc4c02"],DET:["0076b6","bbbbbb"],GB:["204e32","ffb612"],
    HOU:["021018","eb0028"],IND:["003b75","ffffff"],JAX:["007487","d7a22a"],
    KC:["e31837","ffb612"],LAC:["0080c6","ffc20e"],LAR:["003594","ffd100"],
    LV:["000000","a5acaf"],MIA:["008e97","fc4c02"],MIN:["4f2683","ffc62f"],
    NE:["002a5c","c60c30"],NO:["d3bc8d","000000"],NYG:["003c7f","c9243f"],
    NYJ:["115740","ffffff"],PHI:["06424d","000000"],PIT:["000000","ffb612"],
    SEA:["002a5c","69be28"],SF:["aa0000","b3995d"],TB:["bd1c36","3e3a35"],
    TEN:["4495d2","001532"],WSH:["5a1414","ffb612"]
  };
  const rgb = hex => hex.match(/../g).map(v => parseInt(v,16));
  const hex = channels => "#"+channels.map(v=>Math.round(v).toString(16).padStart(2,"0")).join("");
  function luminance(color) {
    const values=rgb(color.replace("#","")).map(v=>v/255).map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4);
    return values[0]*.2126+values[1]*.7152+values[2]*.0722;
  }
  function readable(color) {
    let channels=rgb(color.replace("#",""));
    while ((luminance(hex(channels))+.05)/(luminance("#111317")+.05)<4.5) {
      channels=channels.map(v=>v+(255-v)*.12);
    }
    return hex(channels);
  }
  function theme(team) {
    const pair=colors[team]||["738499","b8c6d6"];
    const [primary,secondary]=pair.map(v=>"#"+v);
    const channels=rgb(pair[1]);
    const colorful=Math.max(...channels)-Math.min(...channels)>35;
    return {primary,secondary,accent:readable(colorful?secondary:primary)};
  }
  function playerTeam(profile) {
    const player=profile?.player;
    if (!player) return null;
    if (player.is_current) return colors[player.team]?player.team:null;
    const observed=[...new Set((profile.observed_teams||[]).filter(t=>colors[t]))];
    if (observed.length===1) return observed[0];
    if (observed.length>1) return null;
    return colors[player.team]?player.team:null;
  }
  function applyTheme(node,team) {
    const t=theme(team);
    node.style.setProperty("--team-primary",t.primary);
    node.style.setProperty("--team-secondary",t.secondary);
    node.style.setProperty("--team-accent",t.accent);
    node.dataset.team=team||"NFL";
    return t;
  }
  function rankInfo(metric) {
    const direction=metric?.direction||metric?.percentile?.direction;
    const rank=metric?.percentile;
    const qualified=rank&&Number.isFinite(rank.value)&&rank.value>=0&&rank.value<=100;
    const contextual=direction==="context";
    return {
      value:qualified?rank.value:null,
      contextual,
      directionLabel:direction==="lower"?"↓ Lower raw value is better":direction==="higher"?"↑ Higher raw value is better":"Context · no better direction",
      rankLabel:qualified?(contextual?"Frequency percentile":"Strength percentile"):"Not ranked",
      explanation:contextual?"Higher percentile means more of this statistic, not better performance.":
        "Higher strength percentile is always better. Defensive allowed metrics are already reversed.",
      band:!qualified?"Insufficient sample":contextual?"Context only":rank.value>=75?"Top quarter":rank.value>=50?"Above median":rank.value>=25?"Below median":"Bottom quarter"
    };
  }
  function matchupOutlook(factor,offense,defense) {
    const a=rankInfo({...factor.offense,percentile:factor.offense_percentile});
    const b=rankInfo({...factor.defense,percentile:factor.defense_percentile});
    // The explosive and red-zone comparisons deliberately use different
    // denominators/populations. Never combine these into a numerical result.
    const comparable=typeof factor.comparable==="boolean"?factor.comparable:["Passing efficiency","Rushing success","Sack exposure"].includes(factor.factor);
    if (!comparable) return {status:"CONTEXT_ONLY",leader:null,title:"Different measurement bases",
      detail:"Read the two profiles separately; these rates cannot be combined.",projection:null};
    if (a.value===null||b.value===null||a.contextual||b.contextual) return {
      status:"UNAVAILABLE",leader:null,title:factor.comparable===true&&factor.offense?.value!=null&&factor.defense?.value!=null?"Observed matchup context":"More evidence needed",
      detail:factor.comparable===true?"The definitions match. Observed values are shown; more complete samples are needed for qualified ranks.":"Both sides need qualified directional ranks before a profile comparison.",projection:null};
    const difference=a.value-b.value;
    const leader=difference>0?offense:difference<0?defense:null;
    return {status:"DESCRIPTIVE_ONLY",leader,
      title:leader?`${leader} profile lead`:"Even profile ranks",
      detail:`${Math.abs(difference).toFixed(1)} percentile-point gap across each metric’s own league cohort.`,
      projection:null};
  }
  const api={colors,theme,playerTeam,applyTheme,rankInfo,matchupOutlook,luminance};
  if (typeof module!=="undefined"&&module.exports) module.exports=api;
  root.TeamVisuals=api;
})(globalThis);
