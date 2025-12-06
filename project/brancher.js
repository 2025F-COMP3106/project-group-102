// bracnher.js - Direct state construction (no replay)
const fs = require('fs');
const Sim = require('pokemon-showdown');
const { Worker, isMainThread, parentPort, workerData } = require('worker_threads');

let topMovesData = {};  // Global declaration

// Load moves
const movesData = JSON.parse(fs.readFileSync('../data/gen9_moves.json', 'utf8'));
console.log('✅ Loaded gen9_moves.json');
const moveAccCache = {};

// Add hash function at top of file
function hashString(str) {
  let hash = 0;
  for (let i = 0; i < str.length; i++) {
    const char = str.charCodeAt(i);
    hash = ((hash << 5) - hash) + char;
    hash = hash & hash;
  }
  return Math.abs(hash) % 0x10000;
}

// Load full team from myteam.txt
function loadMyTeam() {
  try {
    const teamText = fs.readFileSync('myteam.txt', 'utf8');
    return parseShowdownTeam(teamText);
  } catch (err) {
    console.warn('⚠ Could not load myteam.txt, using state data only');
    return null;
  }
}

// Parse Showdown team format
function parseShowdownTeam(text) {
  const pokemon = [];
  
  // Split by Pokemon - each starts with a line containing "@"
  const lines = text.split('\n');
  let currentPokemon = null;
  
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i].trim();
    
    if (!line) continue;
    
    // New Pokemon starts with "Species @ Item"
    if (line.includes('@') && !line.startsWith('-') && !line.includes(':')) {
      // Save previous pokemon
      if (currentPokemon && currentPokemon.species) {
        pokemon.push(currentPokemon);
      }
      
      // Parse: "Species @ Item" or "Species (Gender) @ Item"
      const match = line.match(/^([^@(]+?)(?:\s*\([MF]\))?\s*@\s*(.+)$/);
      if (match) {
        currentPokemon = {
          species: match[1].trim(),
          item: match[2].trim(),
          ability: '',
          nature: 'Serious',
          evs: { hp: 0, atk: 0, def: 0, spa: 0, spd: 0, spe: 0 },
          moves: []
        };
      }
    } else if (currentPokemon) {
      // Parse properties
      if (line.startsWith('Ability:')) {
        currentPokemon.ability = line.replace('Ability:', '').trim();
      } else if (line.startsWith('Tera Type:')) {
        currentPokemon.teraType = line.replace('Tera Type:', '').trim();
      } else if (line.startsWith('EVs:')) {
        const evParts = line.replace('EVs:', '').split('/');
        for (const part of evParts) {
          const match = part.trim().match(/(\d+)\s+(\w+)/);
          if (match) {
            const stat = match[2].toLowerCase();
            currentPokemon.evs[stat] = parseInt(match[1]);
          }
        }
      } else if (line.includes('Nature')) {
        currentPokemon.nature = line.replace('Nature', '').trim();
      } else if (line.startsWith('-')) {
        currentPokemon.moves.push(line.substring(1).trim());
      }
    }
  }
  
  // Don't forget the last Pokemon
  if (currentPokemon && currentPokemon.species) {
    pokemon.push(currentPokemon);
  }
  
  return pokemon;
}

// Load Smogon sets and MyTeam
let smogonSets = {};
try {
  smogonSets = JSON.parse(fs.readFileSync('smogon_ou_sets.json', 'utf8'));
  console.log('✅ Loaded Smogon OU sets');
} catch (err) {
  console.warn('⚠ Could not load smogon_ou_sets.json');
}

const myTeam = loadMyTeam();
if (myTeam) {
  console.log('✅ Loaded myteam.txt with', myTeam.length, 'Pokemon');
}


function parseMoveNameFromAction(action) {
  const teraMatch = action.match(/^([a-z]+);(.+)$/);
  const core = teraMatch ? teraMatch[2] : action;
  const moveMatch = core.match(/^([^,]+)/);
  const moveName = moveMatch ? moveMatch[1].trim() : null;
  if (!moveName) return null;
  return moveName.toLowerCase().replace(/ /g, '-');
}

function normalizeMoveId(id) {
  if (!id) return '';
  // Match Showdown-style move IDs in gen9_moves.json
  return id
    .toLowerCase()
    .replace(/\s+/g, '')      // remove spaces
    .replace(/_/g, '')        // remove underscores
    .replace(/-/g, '');       // remove dashes
}

function getMoveAccuracyFromAction(action) {
  if (!action) return 1.0;

  // Extract raw ID or name
  let rawId = action;
  if (typeof action !== 'string' || action.includes(';') || action.includes(' ')) {
    rawId = parseMoveNameFromAction(action);
  }
  if (!rawId) return 1.0;

  // Normalize once
  const norm = normalizeMoveId(rawId);

  // Cached?
  if (moveAccCache[norm] != null) {
    return moveAccCache[norm];
  }

  // Direct lookup first
  let move = movesData[norm] || null;

  // Fallback: single pass over keys only on cache miss
  if (!move) {
    const key = Object.keys(movesData).find(k => normalizeMoveId(k) === norm);
    if (key) move = movesData[key];
  }

  const rawAcc = move && move.accuracy != null ? move.accuracy : 100;
  const acc = rawAcc / 100;

  moveAccCache[norm] = acc;
  return acc;
}


function getSmogonSet(species) {
  const baseName = species.split('-')[0];
  if (smogonSets[species]) return smogonSets[species];
  if (smogonSets[baseName]) return smogonSets[baseName];
  return null;
}

function toID(text) {
  if (!text) return '';
  return ('' + text).toLowerCase().replace(/[^a-z0-9]+/g, '');
}

function packTeam(pokemonList, fullTeamData = null, useTopMovesData = false) {
  return pokemonList.map((p, i) => {
    const species = p.species;

    let fullData = null;
    if (fullTeamData) {
      fullData = fullTeamData.find(td => td.species === species);
    }
    const smogonSet = getSmogonSet(species);

    // --- ABILITY: null = unknown, "" = explicitly none/suppressed, else known ---
    let abilityRaw;
    if (p.ability === "") {
      abilityRaw = "";           // explicitly no / suppressed ability
    } else if (p.ability == null) {
      abilityRaw = null;         // completely unknown
    } else {
      abilityRaw = p.ability;    // known from state only
    }
    const ability = abilityRaw == null ? "" : toID(abilityRaw);

    // --- ITEM: null = unknown, "" = explicitly gone/knocked off, else known ---
    let itemRaw;
    if (p.item === "") {
      itemRaw = "";              // explicitly no item (knocked off / consumed and tracked)
    } else if (p.item == null) {
      itemRaw = null;            // unknown item
    } else {
      itemRaw = p.item;          // known from state only
    }
    const item = itemRaw == null ? "" : toID(itemRaw);

    let moves;
    if (useTopMovesData && topMovesData[toID(species)]?.moves) {
      const topMoveIds = Object.keys(topMovesData[toID(species)].moves);
      moves = topMoveIds.join(',');
    } else if (fullData && fullData.moves && fullData.moves.length > 0) {
      moves = fullData.moves.map(m => toID(m)).join(',');
    } else if (p.moves && p.moves.length > 0) {
      moves = p.moves.map(m => toID(m.name)).join(',');
    } else {
      moves = [];
    }

    const nature = fullData?.nature || smogonSet?.nature || 'Serious';

    let evs = '0,0,0,0,0,0';
    if (fullData?.evs) {
      const e = fullData.evs;
      evs = [e.hp||0, e.atk||0, e.def||0, e.spa||0, e.spd||0, e.spe||0].join(',');
    } else if (smogonSet?.evs) {
      const e = smogonSet.evs;
      evs = [e.hp||0, e.atk||0, e.def||0, e.spa||0, e.spd||0, e.spe||0].join(',');
    }

    const ivs = '31,31,31,31,31,31';
    const gender = '';
    const shiny = 'N';
    const level = '';
    const happiness = '';

    return `${species}||${item}|${ability}|${moves}|${nature}|${evs}||${ivs}|${shiny}|${level}||${happiness}||||`;
  }).join(']');
}


async function buildBattleFromLastState(state) {
  const stream = new Sim.BattleStream();
  
  // Pack teams
  const p1Team = packTeam(state.player1.pokemon, myTeam, false);     // P1: myteam.txt
  const p2Team = packTeam(state.player2.pokemon, null, true);        // P2: top-moves.txt
  
  // Start battle
  stream.write(`>start {"formatid":"gen9ou"}`);
  stream.write(`>player p1 {"name":"Player1","team":"${p1Team}"}`);
  stream.write(`>player p2 {"name":"Player2","team":"${p2Team}"}`);
  
  // Team preview
  while (true) {
    const chunk = await readWithTimeout(stream);
    if (chunk === null) break;
    if (String(chunk).includes('|teampreview')) {
      // Check if we have active pokemon (lead state vs battle state)
      const p1Active = state.environment.active_pokemon?.p1;
      const p2Active = state.environment.active_pokemon?.p2;
      
      if (p1Active && p2Active) {
        // Battle already started - specific order based on active
        const p1Idx = state.player1.pokemon.findIndex(p => p.species === p1Active);
        const p2Idx = state.player2.pokemon.findIndex(p => p.species === p2Active);
        
        let p1Order = [p1Idx, ...Array.from({length: 6}, (_, i) => i).filter(i => i !== p1Idx)].map(i => i + 1).join('');
        let p2Order = [p2Idx, ...Array.from({length: 6}, (_, i) => i).filter(i => i !== p2Idx)].map(i => i + 1).join('');
        
        stream.write(`>p1 team ${p1Order}`);
        stream.write(`>p2 team ${p2Order}`);
      } else {
        // Lead state - just use default order (1-6)
        stream.write('>p1 team 123456');
        stream.write('>p2 team 123456');
      }
      break;
    }
  }
  
  // Wait for start
  for (let i = 0; i < 100; i++) {
    const chunk = await readWithTimeout(stream);
    if (chunk === null) break;
    if (stream.battle?.sides?.[0]?.active?.[0] && stream.battle?.sides?.[1]?.active?.[0]) {
      break;
    }
  }
  
  const battle = stream.battle;
  
  // Set HP from state - match by species name
  for (let i = 0; i < battle.sides[0].pokemon.length; i++) {
    const battleMon = battle.sides[0].pokemon[i];
    const stateMon = state.player1.pokemon.find(p => p.species === battleMon.species.name);
    if (stateMon) {
      battleMon.hp = Math.round(battleMon.maxhp * stateMon.health / 100);
      if (stateMon.health === 0) battleMon.fainted = true;
      
      // ✅ ADD THIS: Apply status effects from state
      if (stateMon.status_effects) {
        const statusMap = {
          'poison': 'psn',
          'burn': 'brn',
          'paralysis': 'par',
          'freeze': 'frz',
          'sleep': 'slp',
          'toxic': 'tox'
        };
        battleMon.status = statusMap[stateMon.status_effects] || stateMon.status_effects;
        
        // Handle toxic counter
        if (stateMon.status_effects === 'toxic' && stateMon.toxic_counter) {
          battleMon.statusData = { toxicTurns: stateMon.toxic_counter };
        }
      }
    }
  }
  

  for (let i = 0; i < battle.sides[1].pokemon.length; i++) {
    const battleMon = battle.sides[1].pokemon[i];
    const stateMon = state.player2.pokemon.find(p => p.species === battleMon.species.name);
    if (stateMon) {
      battleMon.hp = Math.round(battleMon.maxhp * stateMon.health / 100);
      if (stateMon.health === 0) battleMon.fainted = true;
      if (stateMon.status_effects) {
        const statusMap = {
          'poison': 'psn',
          'burn': 'brn',
          'paralysis': 'par',
          'freeze': 'frz',
          'sleep': 'slp',
          'toxic': 'tox'
        };
        battleMon.status = statusMap[stateMon.status_effects] || stateMon.status_effects;
        
        if (stateMon.status_effects === 'toxic' && stateMon.toxic_counter) {
          battleMon.statusData = { toxicTurns: stateMon.toxic_counter };
        }
      }
    }
  }



    // Set tera types (KNOWN from state) for BOTH sides - display only, don't block actions
  // Set tera types for P1 from myteam.txt (KNOWN, 1 type only)
  for (let i = 0; i < battle.sides[0].pokemon.length; i++) {
    const battleMon = battle.sides[0].pokemon[i];
    
    // ✅ Priority 1: myteam.txt
    if (myTeam) {
      const teamMon = myTeam.find(t => t.species === battleMon.species.name);
      if (teamMon?.teraType) {
        battleMon.teraType = teamMon.teraType;
      }
    }
    
    // ✅ Fallback: state file (only if myteam.txt didn't provide it)
    if (!battleMon.teraType) {
      const stateMon = state.player1.pokemon.find(p => p.species === battleMon.species.name);
      if (stateMon?.tera_type) {
        battleMon.teraType = stateMon.tera_type;
      }
    }
  }


  for (let i = 0; i < battle.sides[1].pokemon.length; i++) {  // P2
    const battleMon = battle.sides[1].pokemon[i];
    const stateMon = state.player2.pokemon.find(p => p.species === battleMon.species.name);
    if (stateMon?.tera_type) {
      battleMon.teraType = stateMon.tera_type;  // Display known Tera type
    }
  }
  // After setting HP, add this:
  if (battle.sides[0].canTerastallize === undefined) battle.sides[0].canTerastallize = true;
  if (battle.sides[1].canTerastallize === undefined) battle.sides[1].canTerastallize = true;

  // Then disable for those who used Tera
  if (state.environment.tera_used?.p1) battle.sides[0].canTerastallize = false;
  if (state.environment.tera_used?.p2) battle.sides[1].canTerastallize = false;
  // Tera USAGE (separate from type knowledge) - blocks actions if USED
  return battle;
}

// ============================================================================
// BRANCHING LOGIC - Generate all possible actions and outcomes
// ============================================================================

// Add this NEW function after hashString
// Update parseTopMoves to accept species name
function parseTopMoves(text, activeP2Species, usePJoint = true) {
  const data = {};
  
  if (!activeP2Species) {
    console.log('⚠ No active P2 species provided');
    return data;
  }
  
  const species = toID(activeP2Species);
  data[species] = { moves: {}, switches: {} };
  
  const sections = text.split(/Turn type:/);
  
  for (const section of sections) {
    // Parse move section
    if (section.includes('MOVE') && section.includes('top moves:')) {
      const movesSection = section.split('top moves:')[1];
      if (!movesSection) continue;
      const moveLines = movesSection.split(/Turn type:|top switches:/)[0];
      
      const moveRegex = /^\s{4,}([A-Z][A-Za-z\s]+):\s*P\(cond\)=([\d.]+),\s*P\(joint\)=([\d.]+)/gm;
      
      let match;
      while ((match = moveRegex.exec(moveLines)) !== null) {
        const moveName = match[1].trim();
        const pCond = parseFloat(match[2]);
        const pJoint = parseFloat(match[3]);
        
        const probability = usePJoint ? pJoint : pCond;
        data[species].moves[toID(moveName)] = probability;
      }
    }
    
    // Parse switch section
    if (section.includes('SWITCH') && section.includes('top switches:')) {
      const switchesSection = section.split('top switches:')[1];
      if (!switchesSection) continue;
      const switchLines = switchesSection.split(/Turn type:/)[0];
      
      const switchRegex = /^\s{4,}([A-Za-z0-9\-]+):\s*P\(cond\)=([\d.]+),\s*P\(joint\)=([\d.]+)/gm;
      
      let match;
      while ((match = switchRegex.exec(switchLines)) !== null) {
        const switchName = match[1].trim();
        const pCond = parseFloat(match[2]);
        const pJoint = parseFloat(match[3]);
        
        const probability = usePJoint ? pJoint : pCond;
        data[species].switches[toID(switchName)] = probability;
      }
    }
  }
  
  return data;
}

function parseTopSwitches(text, activeP2Species, usePJoint = true) {  // ← Add parameter
  const data = {};
  
  const species = toID(activeP2Species);
  data[species] = { moves: {}, switches: {} };
  
  const sections = text.split(/Turn type:/);
  for (const section of sections) {
    // Parse move section
    
    // Parse switch section
    if (section.includes('SWITCH') && section.includes('top switches:')) {
      const switchesSection = section.split('top switches:')[1];
      if (!switchesSection) continue;
      const switchLines = switchesSection.split(/Turn type:/)[0];
      
      // Parse BOTH values
      const switchRegex = /^\s{4,}([A-Za-z0-9\-]+):\s*P\(cond\)=([\d.]+),\s*P\(joint\)=([\d.]+)/gm;
      
      let match;
      
      while ((match = switchRegex.exec(switchLines)) !== null) {
        
        const switchName = match[1].trim();
        const pCond = parseFloat(match[2]);
        const pJoint = parseFloat(match[3]);
        
        // Choose which to use
        const probability = usePJoint ? pJoint : pCond;
        
        data[species].switches[switchName] = probability;
      }
    }
  }
  
  return data;
}

function parseTopTera(text, usePJoint = true) {  
  const data = {}; 
  const sections = text.split(/Turn type:/);

  for (const section of sections) {
    if (section.startsWith(" TERA_MOVE")) {
      const teraSection = section.split("top tera types:")[1];
      if (!teraSection) continue;

      const teraLines = teraSection.split(/Turn type:/)[0];

      // ✅ Updated regex to capture BOTH probabilities
      const teraRegex = /^\s{4,}([a-zA-Z]+):\s*P\(cond\)=([\d.]+),\s*P\(joint\)=([\d.]+)/gm;
      let match;
      while ((match = teraRegex.exec(teraLines)) !== null) {
        const teraType = match[1].trim();
        const pCond = parseFloat(match[2]);
        const pJoint = parseFloat(match[3]);
        
        // ✅ Choose which probability to use
        const probability = usePJoint ? pJoint : pCond;
        data[teraType.toLowerCase()] = probability;  // Store as {type: prob}
      }
    }
  }

  return data; 
}



function toID(text) {
  if (text == null) return '';
  return text.toString().toLowerCase().replace(/[^\w-]/g, '').replace(/^-+|-+$/g, '');
}

// 1. Fix generatePossibleActions - find real slots for predicted moves
function findMoveSlot(sideObj, moveId) {
  const active = sideObj.active[0];
  if (active) {
    const moveIdx = active.moveSlots.findIndex(m => toID(m.move) === moveId);
    if (moveIdx >= 0) return moveIdx + 1;
  }
  return 1; // fallback
}

function generatePossibleActions(battle, side, isOpponent = false) {
  const actions = [];
  const sideObj = battle.sides[side === 'p1' ? 0 : 1];

  if (!sideObj.active[0]) {
    console.error(`No active Pokémon for ${side}`);
    return actions;
  }

  const activeMon = sideObj.active[0];
  const species = activeMon.species.name;
  const speciesId = toID(species);

  // P1: single known Tera from myteam.txt
  const p1TeraAvailable = sideObj.canTerastallize !== false;
  const p1TeraType = activeMon.teraType;
  const p1TeraTypes = p1TeraAvailable && p1TeraType ? [p1TeraType] : [];

  // P2: all Tera types from topTeraData (object)
  const p2TeraAvailable = sideObj.canTerastallize !== false;
  const allTeraTypes = topTeraData ? Object.keys(topTeraData) : [];
  const p2TeraTypes = p2TeraAvailable ? allTeraTypes : [];

  const teraTypes = (isOpponent ? p2TeraTypes : p1TeraTypes) || [];

  // P2 priors from top-moves.txt
  const p2TopMoves   = isOpponent && topMovesData[speciesId] ? topMovesData[speciesId].moves    : null;
  const p2TopSwitches= isOpponent && topMovesData[speciesId] ? topMovesData[speciesId].switches : null;

  // NORMAL MOVES + TERA MOVES
  for (let i = 0; i < activeMon.moveSlots.length; i++) {
    const moveSlot = activeMon.moveSlots[i];
    if (moveSlot.disabled || moveSlot.pp <= 0) continue;

    const moveId = toID(moveSlot.move);

    // Base move prior for P2; 1.0 for P1
    const baseMoveProb =
      p2TopMoves && p2TopMoves[moveId] != null ? p2TopMoves[moveId] : 1.0;

    actions.push({
      type: 'move',
      slot: i + 1,
      moveName: moveSlot.move,
      moveId,
      command: `${side} move ${i + 1}`,
      description: `Use ${moveSlot.move}`,
      probability: baseMoveProb,
    });

    // Tera versions of known moves
    for (const teraType of teraTypes) {
      // For P2, use topTeraData[teraType]; for P1, equal split
      const teraTypeProb =
        isOpponent && topTeraData && topTeraData[teraType] != null
          ? topTeraData[teraType]
          : (teraTypes.length > 0 ? 1.0 / teraTypes.length : 0.0);

      const teraMoveProb = baseMoveProb * teraTypeProb;

      actions.push({
        type: 'tera-move',
        slot: i + 1,
        moveName: `${moveSlot.move} (Tera ${teraType})`,
        moveId,
        teraType,
        command: `${side} move ${i + 1} terastallize`,
        description: `Tera ${teraType} ${moveSlot.move}`,
        probability: teraMoveProb,
      });
    }
  }

  // SWITCHES (baseline 1.0 for P1, priors for P2)
  for (let i = 0; i < sideObj.pokemon.length; i++) {
    const mon = sideObj.pokemon[i];
    if (mon === activeMon || mon.fainted || mon.hp <= 0) continue;

    let switchProb = 1.0;
    if (isOpponent && p2TopSwitches) {
      const switchId = toID(mon.species.name);
      if (p2TopSwitches[switchId] != null) {
        switchProb = p2TopSwitches[switchId];
      }
    }

    actions.push({
      type: 'switch',
      slot: i + 1,
      targetName: mon.species.name,
      command: `${side} switch ${i + 1}`,
      description: `Switch to ${mon.species.name}`,
      probability: switchProb,
    });
  }

  // EXTRA P2 PREDICTED MOVES (not in current moveset)
  if (isOpponent && topMovesData[speciesId]) {
    const topMoves = topMovesData[speciesId].moves;
    const knownMoveIds = activeMon.moveSlots.map(m => toID(m.move));

    const moveProbs = Object.entries(topMoves)
      .sort(([, a], [, b]) => b - a)
      .slice(0, 4);

    for (const [moveId, prob] of moveProbs) {
      if (knownMoveIds.includes(moveId)) continue;

      // Base predicted move
      actions.push({
        type: 'move',
        slot: 1,
        moveName: moveId.replace(/-/g, ' ').replace(/\b\w/g, l => l.toUpperCase()),
        moveId,
        command: `${side} move 1`,
        description: `Use ${moveId.replace(/-/g, ' ')} (predicted ${Math.round(prob * 100)}%)`,
        probability: prob,
      });

      // Tera versions of predicted moves
      if (teraTypes.length > 0) {
        for (const teraType of teraTypes) {
          const teraTypeProb =
            topTeraData && topTeraData[teraType] != null
              ? topTeraData[teraType]
              : 1.0 / teraTypes.length;

          const teraProb = prob * teraTypeProb;

          actions.push({
            type: 'tera-move',
            slot: 1,
            moveName: `${moveId.replace(/-/g, ' ')} Tera ${teraType}`,
            moveId,
            teraType,
            command: `${side} move 1 terastallize`,
            description: `Tera ${teraType} ${moveId.replace(/-/g, ' ')} (predicted ${Math.round(prob * 100)}%)`,
            probability: teraProb,
          });
        }
      }
    }
  }

  if (isOpponent) {
    actions.sort((a, b) => (b.probability || 0) - (a.probability || 0));
  }

  return actions;
}

// Helper function to read with timeout
async function readWithTimeout(stream, timeoutMs = 100) {
  return Promise.race([
    stream.read(),
    new Promise(resolve => setTimeout(() => resolve(null), timeoutMs))
  ]);
}

// Helper to find Pokemon slot number (1-6) by species name
function findPokemonSlot(battle, side, speciesName) {
  for (let i = 0; i < battle.sides[side].pokemon.length; i++) {
    if (battle.sides[side].pokemon[i].species.name === speciesName) {
      return i + 1;
    }
  }
  return -1;
}

async function simulateBranch(baseBattle, p1Action, p2Action, outcome) {
  try {
    const stream = new Sim.BattleStream();
    // Create deterministic seed based on actions and outcome
    const seedValue = hashString(`${p1Action.command}-${p2Action.command}-${outcome.p1Hit}-${outcome.p2Hit}`);
    const seed = [seedValue, seedValue * 2, seedValue * 3, seedValue * 4];
    // Pack teams from base battle
    const p1Team = baseBattle.sides[0].pokemon.map(p => {
      const species = p.species.name;
      const ability = toID(p.ability);
      const item = toID(p.item);
      const moves = p.moveSlots.map(m => toID(m.move)).join(',');
      return `${species}||${item}|${ability}|${moves}|Serious|0,0,0,0,0,0||31,31,31,31,31,31||||||`;
    }).join(']');
    
    const p2Team = baseBattle.sides[1].pokemon.map(p => {
      const species = p.species.name;
      const ability = toID(p.ability);
      const item = toID(p.item);
      const moves = p.moveSlots.map(m => toID(m.move)).join(',');
      return `${species}||${item}|${ability}|${moves}|Serious|0,0,0,0,0,0||31,31,31,31,31,31||||||`;
    }).join(']');
    
    // Start battle
    stream.write(`>start {"formatid":"gen9ou","seed":${JSON.stringify(seed)}}`);  // ← Add seed here
    stream.write(`>player p1 {"name":"Player1","team":"${p1Team}"}`);
    stream.write(`>player p2 {"name":"Player2","team":"${p2Team}"}`);
    
    // Read until team preview
    for (let i = 0; i < 100; i++) {
      const chunk = await readWithTimeout(stream);
      if (chunk === null) break;
      if (String(chunk).includes('|teampreview')) {
        stream.write('>p1 team 123456');
        stream.write('>p2 team 123456');
        break;
      }
    }
    
    // Read until battle starts
    for (let i = 0; i < 100; i++) {
      const chunk = await readWithTimeout(stream);
      if (chunk === null) break;
      if (stream.battle?.sides?.[0]?.active?.[0] && stream.battle?.sides?.[1]?.active?.[0]) {
        break;
      }
    }
    
    const battle = stream.battle;
    
    if (!battle?.sides?.[0]?.active?.[0]) {
      return { battle: null, success: false };
    }
    
    // Copy HP from base battle
    // Copy HP from base battle
    for (let i = 0; i < battle.sides[0].pokemon.length; i++) {
      const baseMon = baseBattle.sides[0].pokemon[i];
      const newMon = battle.sides[0].pokemon[i];
      
      newMon.hp = baseMon.hp;
      
      if (baseMon.status) {
        newMon.status = baseMon.status;
        
        // Copy toxic counter if applicable
        if (baseMon.status === 'tox' && baseMon.statusData) {
          newMon.statusData = { 
            toxicTurns: baseMon.statusData.toxicTurns || 0 
          };
        }
      }
      
      if (baseMon.volatiles) {
        for (const [key, val] of Object.entries(baseMon.volatiles)) {
          newMon.volatiles[key] = val;
        }
      }
    }
    // Repeat for P2
    for (let i = 0; i < battle.sides[1].pokemon.length; i++) {
      const baseMon = baseBattle.sides[1].pokemon[i];
      const newMon = battle.sides[1].pokemon[i];
      
      newMon.hp = baseMon.hp;
      
      if (baseMon.status) {
        newMon.status = baseMon.status;
        if (baseMon.status === 'tox' && baseMon.statusData) {
          newMon.statusData = { toxicTurns: baseMon.statusData.toxicTurns || 0 };
        }
      }
      
      if (baseMon.volatiles) {
        for (const [key, val] of Object.entries(baseMon.volatiles)) {
          newMon.volatiles[key] = val;
        }
      }
    }
    
    // In simulateBranch - AFTER setting up battle, BEFORE submitting moves:
    if (p1Action.teraType) {
      // Force P1 Tera to specific type
      const p1Active = battle.sides[0].active[0];
      if (p1Active) {
        p1Active.teraType = p1Action.teraType;
        battle.sides[0].canTerastallize = false;  // Used
      }
    }

    if (p2Action.teraType) {
      // Force P2 Tera to specific type  
      const p2Active = battle.sides[1].active[0];
      if (p2Active) {
        p2Active.teraType = p2Action.teraType;
        battle.sides[1].canTerastallize = false;
      }
    }

    const originalPRNG = battle.prng;
    let movePhase = null;
    let prngCallsThisMove = 0;
    
    battle.prng = Object.create(originalPRNG);

    // battle.prng = function(start, end) {
    //   return originalPRNG(start, end);
    // };
    
    battle.prng.next = function() {
      prngCallsThisMove++;
      
      // Only override first call (accuracy check)
      if (prngCallsThisMove === 1) {
        if (movePhase === 'p1' && (p1Action.type === 'move' || p1Action.type === 'tera-move')) {
          return outcome.p1Hit ? 0.0 : 0.99;
        } else if (movePhase === 'p2' && (p2Action.type === 'move' || p2Action.type === 'tera-move')) {
          return outcome.p2Hit ? 0.0 : 0.99;
        }
      }
      
      // Use deterministic PRNG for everything else
      return originalPRNG.next();
    };
    
    // Copy other PRNG methods
    battle.prng.randomChance = originalPRNG.randomChance.bind(originalPRNG);
    battle.prng.sample = originalPRNG.sample.bind(originalPRNG);
    battle.prng.random = originalPRNG.random.bind(originalPRNG);
    
    for (const key in originalPRNG) {
      if (typeof originalPRNG[key] === 'function' && !battle.prng[key]) {
        battle.prng[key] = originalPRNG[key].bind(originalPRNG);
      }
    }
    
    // Hook into move execution
    const originalRunMove = battle.runMove;
    if (originalRunMove) {
      battle.runMove = function(moveOrMoveName, pokemon, target, sourceEffect) {
        if (pokemon.side.id === 'p1') movePhase = 'p1';
        else if (pokemon.side.id === 'p2') movePhase = 'p2';
        prngCallsThisMove = 0;
        
        const result = originalRunMove.call(this, moveOrMoveName, pokemon, target, sourceEffect);
        
        movePhase = null;
        prngCallsThisMove = 0;  // ← ADD THIS LINE
        return result;
      };
    }
    
    // Submit moves
    stream.write(`>p1 ${p1Action.command.replace('p1 ', '')}`);
    stream.write(`>p2 ${p2Action.command.replace('p2 ', '')}`);
    
    // Drain output
    for (let i = 0; i < 50; i++) {
      const chunk = await readWithTimeout(stream, 50);
      if (chunk === null) break;
    }
    return { battle, success: true };
    
  } catch (err) {
    console.error('Simulation error:', err.message);
    return { battle: null, success: false };
  }
}

// WORKING VERSION
function getEffectiveHitProb(sideObj, action) {
  if (!action || action.type === 'switch') return 1.0;

  const baseAcc = (action.type === 'move' || action.type === 'tera-move')
    ? getMoveAccuracyFromAction(action.moveId)
    : 1.0;

  const active = sideObj.active[0];
  if (!active || !active.status) return baseAcc;

  const isParalyzed = active.status === 'par';
  const paraMult = isParalyzed ? 0.75 : 1.0;

  return baseAcc * paraMult;
}

function isPivotMove(action) {
  let moveId = '';
  if (typeof action === 'string') {
    moveId = toID(action);
  } else if (action.moveId) {
    moveId = action.moveId;  // already normalized
  } else if (action.move || action.moveName) {
    moveId = toID(action.move || action.moveName);
  }
  
  const pivotMoves = new Set([
    'u-turn', 'volt-switch', 'flip-turn', 'chilly-reception',  // ← WITH DASHES
    'parting-shot', 'baton-pass', 'shed-tail'
  ]);
  
  return pivotMoves.has(moveId);
}

function willMoveKO(battle, attackerAction, attackerSide, defenderSide) {
  // Only check for damaging moves
  if (attackerAction.type !== 'move' || !attackerAction.moveId) return false;
  
  const move = battle.dex.moves.get(attackerAction.moveId);
  if (!move || move.category === 'Status') return false;
  
  const defender = defenderSide.active[0];
  if (!defender || defender.fainted) return false;
  
  const hpPercent = (defender.hp / defender.maxhp) * 100;
  
  return hpPercent <= 30 && move.basePower > 0;
}

function addUserFaintAndSwitchSuffix(baseBattle, newBattle, moveStr, sideId) {
  const sideIdx = sideId === 'p1' ? 0 : 1;

  const baseSide = baseBattle.sides[sideIdx];
  const newSide  = newBattle.sides[sideIdx];

  const baseActive = baseSide.active[0];
  const newActive  = newSide.active[0];

  if (!baseActive || !newActive) return moveStr;

  const userWasAlive = !baseActive.fainted && baseActive.hp > 0;
  const userIsDead   = newActive.fainted || newActive.hp <= 0;

  // Only encode if the *user* on this side fainted
  if (!userWasAlive || !userIsDead) return moveStr;

  // Choose a replacement from this side's bench
  let switchIn = null;
  for (const mon of newSide.pokemon) {
    if (mon === newActive) continue;           // still the dead active
    if (mon.fainted || mon.hp <= 0) continue;  // can't switch to fainted
    switchIn = mon.species.name;
    break;
  }
  if (!switchIn) return moveStr;

  return `${moveStr},faint:${switchIn}`;
}

function actionToMoveNotation(actionObj, side, battle) {
  if (!actionObj) return '';
  
  // Handle your exact action format from generatePossibleActions
  if (actionObj.type === 'switch') {
    return `switch:${actionObj.targetName || 'Unknown'}`;
  }
  
  let moveStr = actionObj.moveName || actionObj.moveId || actionObj.move || 'unknown';
  
  // Handle Tera moves (your format: "electric;First Impression")
  if (actionObj.teraType) {
    const cleanMove = moveStr.replace(/\s*\(Tera\s+\w+\)/i, '').trim();
    return `${actionObj.teraType};${cleanMove}`;
  }
  
  // Handle raw move strings from your JSON (like "U-turn")
  if (typeof actionObj === 'string') {
    return actionObj;
  }
  
  return moveStr;
}

// EV calculation with probability and faint suffix
async function calculateBranchingEV(battle, evalFunction) {
  const p1Actions = generatePossibleActions(battle, 'p1', false);
  const p2Actions = generatePossibleActions(battle, 'p2', true);
  console.log(`branches for ${p1Actions.length} P1 actions × ${p2Actions.length} P2 actions`);

  const results = [];
  const p1LegalSwitches = getLegalSwitchInsForSide(battle, 'p1');
  const p2LegalSwitches = getLegalSwitchInsForSide(battle, 'p2');

  for (const p1Action of p1Actions) {
    let totalEV = 0;
    let totalProb = 0;
    const branchResults = [];

    const p1IsPivot = isPivotMove(p1Action);
    const p1PivotTargets = p1IsPivot ? p1LegalSwitches : [null];

    for (const p2Action of p2Actions) {
      const p2IsPivot = isPivotMove(p2Action);
      const p2PivotTargets = p2IsPivot ? p2LegalSwitches : [null];

      // Behavioral prior from top-moves / top-tera / switches:
      const p2Prior = p2Action.probability || 0;   // <-- used only for p2_action_prior

      const p1Acc = getEffectiveHitProb(battle.sides[0], p1Action);
      const p2Acc = getEffectiveHitProb(battle.sides[1], p2Action);
      
      const outcomeBranches = [
        { p1Hit: true,  p2Hit: true,  prob: p1Acc * p2Acc },
        { p1Hit: true,  p2Hit: false, prob: p1Acc * (1 - p2Acc) },
        { p1Hit: false, p2Hit: true,  prob: (1 - p1Acc) * p2Acc },
        { p1Hit: false, p2Hit: false, prob: (1 - p1Acc) * (1 - p2Acc) },
      ];

      for (const outcome of outcomeBranches) {
        if (outcome.prob === 0) continue;

        const result = await simulateBranch(battle, p1Action, p2Action, outcome);
        if (!result.success || !result.battle) continue;

        const baseBattle = result.battle;  // Keep original for reference

        // P1 pivot/faint targets (unified)
        const p1LegalSwitches2 = getLegalSwitchInsForSide(baseBattle, 'p1');
        const p1SwitchTargets = p1IsPivot ? p1LegalSwitches2 : [null];

        // P2 pivot/faint targets (unified)  
        const p2LegalSwitches2 = getLegalSwitchInsForSide(baseBattle, 'p2');
        const p2SwitchTargets = p2IsPivot ? p2LegalSwitches2 : [null];

        for (const p1Target of p1SwitchTargets) {
          for (const p2Target of p2SwitchTargets) {
            const newBattle = result.battle;

            let p2MoveStr = actionToMoveNotation(p2Action, 'p2', baseBattle);
            let p1MoveStr = actionToMoveNotation(p1Action, 'p1', baseBattle);

            // ---------- P1 PIVOT BRANCHES ----------
            if (isPivotMove(p1Action)) {
              const p1Targets = getLegalSwitchInsForSide(newBattle, 'p1');

              for (const pivotTarget of p1Targets) {
                let pivotP1MoveStr = p1MoveStr;

                if (pivotP1MoveStr.includes(';')) {
                  const [tera, move] = pivotP1MoveStr.split(';');
                  pivotP1MoveStr = `${tera};${move}:${pivotTarget}`;
                } else {
                  pivotP1MoveStr = `${pivotP1MoveStr}:${pivotTarget}`;
                }

                const side = newBattle.sides[0];
                const targetMon = side.pokemon.find(mon => mon.species.name === pivotTarget);
                if (targetMon && !targetMon.fainted && targetMon.hp > 0) {
                  side.active[0] = targetMon;
                }

                const targetProb = 1.0 / p1Targets.length;
                // Full branch probability = outcome * P(p1 action) * P(p2 prior) * split
                const combinedProb = outcome.prob *
                                     (p1Action.probability || 1.0) *
                                     p2Prior *
                                     targetProb;

                const evaluation = 0;
                branchResults.push({
                  p2_action_prior: p2Prior,   // only the behavioral prior
                  p1_action: { player: 'p1', move: pivotP1MoveStr },
                  p2_action: { player: 'p2', move: p2MoveStr },
                  evaluation,
                  probability: combinedProb,  // full branch prob
                  state: battleStateToParserJSON(newBattle, null, [
                    { player: 'p1', move: pivotP1MoveStr },
                    { player: 'p2', move: p2MoveStr },
                  ]),
                });

                totalEV += evaluation * combinedProb;
                totalProb += combinedProb;
              }
            } else {
              // Non-pivot: single P1 branch
              p1MoveStr = addUserFaintAndSwitchSuffix(battle, newBattle, p1MoveStr, 'p1');
            }

            // ---------- P2 PIVOT BRANCHES ----------
            if (p2IsPivot) {
              const p2Targets = getLegalSwitchInsForSide(newBattle, 'p2');
              
              for (const p2Target2 of p2Targets) {
                let pivotP2MoveStr = p2MoveStr;

                if (pivotP2MoveStr.includes(';')) {
                  const [tera, move] = pivotP2MoveStr.split(';');
                  pivotP2MoveStr = `${tera};${move}:${p2Target2}`;
                } else {
                  pivotP2MoveStr = `${pivotP2MoveStr}:${p2Target2}`;
                }

                const p2Side = newBattle.sides[1];
                const p2TargetMon = p2Side.pokemon.find(mon => mon.species.name === p2Target2);
                if (p2TargetMon && !p2TargetMon.fainted && p2TargetMon.hp > 0) {
                  p2Side.active[0] = p2TargetMon;
                }

                const evaluation = evalFunction ? evalFunction(newBattle) : 0;
                const targetProb = 1.0 / (p1SwitchTargets.length * p2Targets.length);
                const combinedProb = outcome.prob *
                                     (p1Action.probability || 1.0) *
                                     p2Prior *
                                     targetProb;

                branchResults.push({
                  p2_action_prior: p2Prior,
                  p1_action: { player: 'p1', move: p1MoveStr },
                  p2_action: { player: 'p2', move: pivotP2MoveStr },
                  evaluation,
                  probability: combinedProb,
                  state: battleStateToParserJSON(newBattle, null, [
                    { player: 'p1', move: p1MoveStr },
                    { player: 'p2', move: pivotP2MoveStr },
                  ]),
                });

                totalEV += evaluation * combinedProb;
                totalProb += combinedProb;
              }
            } else {
              // ---------- KO-ON-P2 BRANCHES ----------
              const willKOP2 = !p2IsPivot &&
                               p1Action.type === 'move' &&
                               willMoveKO(baseBattle, p1Action, baseBattle.sides[0], baseBattle.sides[1]);

              if (willKOP2) {
                const p2Switches = getLegalSwitchInsForSide(newBattle, 'p2');

                for (const p2SwitchTarget of p2Switches) {
                  const p2SwitchMoveStr = `${p2MoveStr},faint:${p2SwitchTarget}`;

                  const evaluation = evalFunction ? evalFunction(newBattle) : 0;
                  const targetProb = 1.0 / p2Switches.length;
                  const combinedProb = outcome.prob *
                                       (p1Action.probability || 1.0) *
                                       p2Prior *
                                       targetProb;

                  branchResults.push({
                    p2_action_prior: p2Prior,
                    p1_action: { player: 'p1', move: p1MoveStr },
                    p2_action: { player: 'p2', move: p2SwitchMoveStr },
                    evaluation,
                    probability: combinedProb,
                    state: battleStateToParserJSON(newBattle, null, [
                      { player: 'p1', move: p1MoveStr },
                      { player: 'p2', move: p2SwitchMoveStr },
                    ]),
                  });

                  totalEV += evaluation * combinedProb;
                  totalProb += combinedProb;
                }
              } else {
                // Non-pivot P2: normal faint logic
                p2MoveStr = addUserFaintAndSwitchSuffix(baseBattle, newBattle, p2MoveStr, 'p2');
              }
            }

            // ---------- FALLBACK / NON-SPECIAL BRANCH ----------
            if (p2IsPivot && p2Target) {
              const side = newBattle.sides[1];
              const targetMon = side.pokemon.find(mon => mon.species.name === p2Target);
              if (targetMon && !targetMon.fainted && targetMon.hp > 0) {
                side.active[0] = targetMon;
              }
              p2MoveStr = `${p2MoveStr},faint:${p2Target}`;
            } else {
              p2MoveStr = addUserFaintAndSwitchSuffix(baseBattle, newBattle, p2MoveStr, 'p2');
            }

            const evaluation = evalFunction ? evalFunction(newBattle) : 0;
            const switchProb = 1.0 / (p1SwitchTargets.length * p2SwitchTargets.length);
            const combinedProb = outcome.prob *
                                 (p1Action.probability || 1.0) *
                                 p2Prior *
                                 switchProb;

            branchResults.push({
              p2_action_prior: p2Prior,
              p1_action: { player: 'p1', move: p1MoveStr },
              p2_action: { player: 'p2', move: p2MoveStr },
              evaluation,
              probability: combinedProb,
              state: battleStateToParserJSON(newBattle, null, [
                { player: 'p1', move: p1MoveStr },
                { player: 'p2', move: p2MoveStr },
              ]),
            });

            totalEV += evaluation * combinedProb;
            totalProb += combinedProb;
          }
        }
      }
    }

    results.push({
      action: p1Action.description,
      command: p1Action.command,
      expectedValue: totalProb > 0 ? totalEV / totalProb : 0,
      totalProbability: totalProb,
      branches: branchResults,
    });
  }

  results.sort((a, b) => b.expectedValue - a.expectedValue);
  return results;
}

// ============================================================================
// CONVERT BATTLE STATE TO PARSER.PY JSON FORMAT
// ============================================================================

// Convert showdown battle to parser.py format

function battleStateToParserJSON(battle, p2Action, prevActions = [], p2NextMove = null, winner = null, avgRating = 1500) {
  // Safety check for battle.sides
  if (!battle || !battle.sides || battle.sides.length < 2) {
    return null;
  }
  // Helper function to extract pokemon data
  function pokemonToJSON(mon) {
    // Extract type array
    const types = [];
    if (mon.types && mon.types.length > 0) {
      for (const t of mon.types) {
        if (t) types.push(t.toLowerCase());
      }
    } else if (mon.baseTypes && mon.baseTypes.length > 0) {
      for (const t of mon.baseTypes) {
        if (t) types.push(t.toLowerCase());
      }
    }

    // Extract moves with PP
    const moves = mon.moveSlots.map(slot => ({
      name: slot.move,
      max_pp: slot.maxpp || 32,
      current_pp: slot.pp || slot.maxpp || 32
    }));

    // Calculate health percentage
    const healthPercent = mon.maxhp > 0 ? Math.min(Math.round((mon.hp / mon.maxhp) * 100), 100) : 0;

    // Extract stat changes
    let statChanges = null;
    if (mon.boosts) {
      const boosts = {};
      for (const [stat, value] of Object.entries(mon.boosts)) {
        if (value !== 0) {
          boosts[stat] = value;
        }
      }
      if (Object.keys(boosts).length > 0) {
        statChanges = boosts;
      }
    }

    // Extract status effects
    let statusEffects = null;

    if (mon.status) {
      const statusMap = {
        'psn': 'poison',
        'brn': 'burn',
        'par': 'paralysis',
        'frz': 'freeze',
        'slp': 'sleep',
        'tox': 'toxic'
      };
      statusEffects = statusMap[mon.status] || mon.status;
    }

  // Extract volatile effects
  const volatileEffects = {};
  if (mon.volatiles) {
    for (const [key, vol] of Object.entries(mon.volatiles)) {
      const id = key.toLowerCase();

      if (id === 'encore') {
        volatileEffects.encore = {
          move: vol.move || (vol.moveid || '').toString(),
          duration: vol.duration || 0
        };
      } else if (id === 'substitute') {
        volatileEffects.substitute = true;
      } else if (id === 'leechseed') {
        volatileEffects.leech_seed = true;
      } else if (id === 'disable') {
        volatileEffects.disable = true;
      } else if (id === 'confusion') {
        volatileEffects.confusion = true;
      } else {
        // For anything not explicitly handled, just record its presence
        volatileEffects[id] = true;
      }
    }
  }

  let item = null;

  // Ability: same semantics as item
  let ability = null;


  return {
    species: mon.species.name,
    type: types.length > 0 ? types : null,
    health: healthPercent,
    status_effects: statusEffects,
    toxic_counter: statusEffects === 'toxic' && mon.statusData ? mon.statusData.toxicTurns || 0 : 0,
    stat_changes: statChanges,
    item: item,
    ability: ability,
    tera_type: mon.teraType ? mon.teraType.toLowerCase() : null,

    moves: moves,
    volatile_effects: volatileEffects
  };
}

  // Extract active pokemon names
  const p1Active = battle.sides[0].active[0] ? battle.sides[0].active[0].species.name : null;
  const p2Active = battle.sides[1].active[0] ? battle.sides[1].active[0].species.name : null;
  // Extract hazards
  const hazards = { p1: {}, p2: {} };
  for (let i = 0; i < battle.sides.length; i++) {
    const side = battle.sides[i];
    const sideKey = i === 0 ? 'p1' : 'p2';
    if (side.sideConditions) {
      for (const [condition, data] of Object.entries(side.sideConditions)) {
        const conditionName = condition.toLowerCase().replace(/([a-z])([A-Z])/g, '$1 $2');
        // Layer-based hazards
        if (condition === 'spikes' || condition === 'toxicspikes') {
          hazards[sideKey][conditionName] = data.layers || 1;
        } else if (condition === 'stealthrock') {
          hazards[sideKey]['stealth rock'] = 1;
        } else {
          // Duration-based conditions (screens, tailwind, etc)
          hazards[sideKey][conditionName] = typeof data === 'object' && data.duration ? data.duration : 1;
        }
      }
    }
  }

  // Extract field effects
  const fieldEffects = [];
  const fieldEffectsTurns = {};
  if (battle.field) {
    for (const [effect, data] of Object.entries(battle.field)) {
      if (effect !== 'pseudoWeather' && data) {
        const effectName = effect.toLowerCase().replace(/([a-z])([A-Z])/g, '$1 $2');
        fieldEffects.push(effectName);
        if (typeof data === 'object' && data.duration) {
          fieldEffectsTurns[effectName] = data.duration;
        }
      }
    }
  }
  // Check pseudo weather for Trick Room, etc
  if (battle.field && battle.field.pseudoWeather) {
    for (const [effect, data] of Object.entries(battle.field.pseudoWeather)) {
      const effectName = effect.toLowerCase().replace(/([a-z])([A-Z])/g, '$1 $2');
      fieldEffects.push(effectName);
      if (typeof data === 'object' && data.duration) {
        fieldEffectsTurns[effectName] = data.duration;
      }
    }
  }

  return {
    player1: {
      pokemon: battle.sides[0].pokemon.map(pokemonToJSON)
    },
    player2: {
      pokemon: battle.sides[1].pokemon.map((mon, idx) => {
                const pokemonData = pokemonToJSON(mon);
        // If P2 action is a switch and this is not the active pokemon, clear moves
if (p2Action && p2Action.description && 
    (p2Action.description.startsWith('switch:') || p2Action.description.toLowerCase().startsWith('switch ')) && 
    idx !== 0) {
  pokemonData.moves = [];
}
                // For P2 active pokemon (first pokemon), only include the move used in this branch
                if (idx === 0 && p2Action && p2Action.description) {
                    // Extract move name from p2Action.description
                    let moveName = p2Action.description.replace(/^Use\s+/i, '');
                    // Handle switches (format: "switch:pokemon")
                    if (moveName.startsWith('switch:') || moveName.toLowerCase().startsWith('switch ')) {
                        moveName = null;
                    } else if (moveName.includes(':') || moveName.includes(',faint:')) {
                        // Handle pivot/faint formats
                        moveName = moveName.split(':')[0].split(',faint:')[0];
                        // Handle Tera prefix (e.g., "bug;U-turn")
                        if (moveName.includes(';')) {
                            moveName = moveName.split(';')[1];
                        }
                    }
                    // Only replace moves if we have a valid move name
                    if (moveName && moveName !== 'null') {
                        // Get move PP from movesData
                        const moveData = movesData[moveName.toLowerCase().replace(/\s+/g, '')];
                        const maxPP = moveData ? moveData.pp : 32;
                        pokemonData.moves = [{
                            name: moveName,
                            max_pp: maxPP,
                            current_pp: Math.max(0, maxPP - 1)  // Decrease PP by 1 since move was used
                        }];
                    }
                }
                return pokemonData;
            })
    },
    environment: {
      weather: battle.weather ? battle.weather : null,
      weather_turns: battle.weatherData && battle.weatherData.duration ? battle.weatherData.duration : null,
      terrain: battle.terrain ? battle.terrain : null,
      terrain_turns: battle.terrainData && battle.terrainData.duration ? battle.terrainData.duration : null,
      field_effects: fieldEffects,
      field_effects_turns: fieldEffectsTurns,
      hazards: hazards,
      active_pokemon: {
    p1: (battle.sides[0].active[0] && !battle.sides[0].active[0].fainted && battle.sides[0].active[0].hp > 0)
      ? battle.sides[0].active[0].species.name
      : null,
    p2: (battle.sides[1].active[0] && !battle.sides[1].active[0].fainted && battle.sides[1].active[0].hp > 0)
      ? battle.sides[1].active[0].species.name
      : null,
  },
      prev_actions: prevActions,
      tera_used: {
        p1: battle.sides[0].canTerastallize === false,
        p2: battle.sides[1].canTerastallize === false
      }
    },
    winner: winner,
    avg_rating: avgRating,
    p2_next_move: p2NextMove
  };
}

function getLegalSwitchInsForSide(battle, sideId) {
  const sideIdx = sideId === 'p1' ? 0 : 1;
  const side = battle.sides[sideIdx];
  const active = side.active[0];

  const candidates = [];
  for (const mon of side.pokemon) {
    if (mon === active) continue;
    if (mon.fainted || mon.hp <= 0) continue;
    candidates.push(mon.species.name);
  }
  return candidates;
}

// Simple evaluation function (you'll replace this with your agent)
function simpleEval(battle) {
  return 0
}

if (require.main === module) {
  (async () => {
    const raw = JSON.parse(fs.readFileSync(process.argv[2] || 'in-state.parsed.json', 'utf8'));
    const states = Array.isArray(raw) ? raw : [raw];
    const lastState = states[states.length - 1];
    const activeP2Species = lastState.environment.active_pokemon.p2;
    // 1. FIRST: Load top-moves.txt 
    try {
      const topMovesText = fs.readFileSync('top-moves.txt', 'utf8');
      topMovesData = parseTopMoves(topMovesText, activeP2Species, true);
      
      const topTeraText = fs.readFileSync('top-moves.txt', 'utf8');
      topTeraData = parseTopTera(topTeraText);
      console.log('✅ Loaded top-moves.txt');
    } catch (err) {
      console.warn('⚠ Could not load top-moves.txt');
    }
    
    // 2. THEN: Build battle (now topMovesData exists!)
    const battle = await buildBattleFromLastState(lastState);
    

      // Check if this is a lead state (no active pokemon)
      const isLeadState = !lastState.environment.active_pokemon.p1 && !lastState.environment.active_pokemon.p2;

      if (isLeadState) {
            console.log('LEAD STATE DETECTED - Generating all 36 lead combinations...');
            const topMovesText = fs.readFileSync('top-moves.txt', 'utf8');
            topMovesData = parseTopSwitches(topMovesText, activeP2Species, true);
            // Get team lists
            const p1Team = lastState.player1.pokemon;
            const p2Team = lastState.player2.pokemon;

            // Generate all 36 lead combinations (6x6)
            const leadBranches = [];
            let branchIndex = 0;

            for (let p1Index = 0; p1Index < p1Team.length; p1Index++) {
                    for (let p2Index = 0; p2Index < p2Team.length; p2Index++) {
                              // Create a copy of the state with this specific lead combination
                              const branchState = JSON.parse(JSON.stringify(lastState));

                              // Set the active pokemon for this branch
                              branchState.environment.active_pokemon.p1 = p1Team[p1Index].species;
                              branchState.environment.active_pokemon.p2 = p2Team[p2Index].species;
                              // Add metadata about this branch
                              const branch = {
                                          p1_action: { player: "p1" , move: "switch:"+p1Team[p1Index].species },
                                          p2_action: { player: "p2" , move: "switch:"+p2Team[p2Index].species },
                                          p2_action_prior:  topMovesData[''].switches[p2Team[p2Index].species],
                                          state: branchState
                                          
                                          };

                              leadBranches.push(branch);
                              branchIndex++;
                            }
                  }

            console.log(`✅ Generated ${leadBranches.length} lead combinations`);

            // Write all lead branches to branches.json
            fs.writeFileSync('branches.json', JSON.stringify(leadBranches, null, 2));
            console.log('✅ Lead state branches written to branches.json');

            return; // Exit early for lead states
          }
    
    const branching = await calculateBranchingEV(battle, simpleEval);

    // Flatten all branchResults from all actions into a single array
    const flatBranches = branching.flatMap(a => a.branches);

    fs.writeFileSync('branches.json', JSON.stringify(flatBranches, null, 2));

    console.log('✅ Branching analysis complete!');
  })();
}
