#!/usr/bin/env node
'use strict';

// Preserve NET semantics using Weave's own pin map; never change its layout.
const fs = require('fs');
const path = require('path');

function preserveMetadata(ascText, netText, core, explicitSubcircuits = []) {
  const { comps } = core.parseNetlist(netText);
  const byName = new Map(comps.map(c => [c.name.toLowerCase(), c]));
  if (byName.size !== comps.length) throw new Error('duplicate NET instance names');
  const classes = core.classifyNets(comps);
  const explicit = new Set(explicitSubcircuits.map(s => s.toLowerCase()));
  const lines = ascText.trimEnd().split(/\r?\n/);
  const labeled = new Set();
  const flagsAt = new Map();
  for (const line of lines) {
    const flag = line.match(/^FLAG\s+(-?\d+)\s+(-?\d+)\s+(\S+)\s*$/);
    if (flag) {
      labeled.add(flag[3].toLowerCase());
      flagsAt.set(`${flag[1]},${flag[2]}`, flag[3].toLowerCase());
    }
  }
  const flags = [];
  const seen = new Set();
  let modelOverrides = 0;
  const output = [];
  for (let i = 0; i < lines.length; i++) {
    const symbol = lines[i].match(/^SYMBOL\s+(\S+)\s+(-?\d+)\s+(-?\d+)\s+([RM](?:0|90|180|270))\s*$/);
    if (!symbol) {
      output.push(lines[i]);
      continue;
    }
    const block = [lines[i]];
    while (i + 1 < lines.length && /^(?:WINDOW|SYMATTR)\s/.test(lines[i + 1])) block.push(lines[++i]);
    const nameLine = block.find(s => /^SYMATTR InstName\s/.test(s));
    const name = nameLine && nameLine.replace(/^SYMATTR InstName\s+/, '').trim().toLowerCase();
    const comp = byName.get(name);
    const pins = core.SYMBOLS[symbol[1]]?.pins;
    if (!comp || seen.has(name) || !pins || pins.length !== comp.nets.length || symbol[1] !== comp.sym) {
      throw new Error(`cannot bind ASC symbol ${name || symbol[1]} to NET pins`);
    }
    seen.add(name);
    comp.nets.forEach((net, index) => {
      if (classes.get(net) !== 'signal' || labeled.has(net.toLowerCase())) return;
      const [dx, dy] = core.rot(pins[index], symbol[4]);
      const x = Number(symbol[2]) + dx;
      const y = Number(symbol[3]) + dy;
      const old = flagsAt.get(`${x},${y}`);
      if (old && old !== net.toLowerCase()) throw new Error(`conflicting node labels at ${x},${y}`);
      flags.push(`FLAG ${x} ${y} ${net}`);
      flagsAt.set(`${x},${y}`, net.toLowerCase());
      labeled.add(net.toLowerCase());
    });
    if (/^X/i.test(comp.name)) {
      // Device symbols can inherit Value2 and an automatic model library.
      // The canonical X-card supplies the model name and any instance params.
      const supplied = explicit.has(comp.value.trim().split(/\s+/)[0].toLowerCase());
      const kept = block.filter(s => !/^SYMATTR Value2(?:\s|$)/.test(s)
        && !(supplied && /^SYMATTR SpiceModel(?:\s|$)/.test(s)));
      kept.push(`SYMATTR Value2 ${comp.value}`);
      if (supplied) {
        kept.push('SYMATTR SpiceModel ""');
        modelOverrides++;
      }
      output.push(...kept);
    } else output.push(...block);
  }
  if (seen.size !== comps.length) throw new Error('ASC is missing NET instances');
  const insertAt = output.findIndex(s => /^SYMBOL\s/.test(s));
  output.splice(insertAt < 0 ? output.length : insertAt, 0, ...flags);
  return { text: output.join('\n') + '\n', labelsAdded: flags.length, modelOverrides };
}

if (require.main === module) {
  try {
    const [weaveDir, net, asc, subcircuits] = process.argv.slice(2);
    const core = require(path.resolve(weaveDir, 'core.js'));
    const result = preserveMetadata(fs.readFileSync(asc, 'latin1'), fs.readFileSync(net, 'utf8'),
      core, JSON.parse(subcircuits));
    fs.writeFileSync(asc, result.text, 'latin1');
    console.log(JSON.stringify({ labels_added: result.labelsAdded, model_overrides: result.modelOverrides }));
  } catch (error) {
    console.error(`ASC metadata preservation failed: ${error.message}`);
    process.exitCode = 1;
  }
}

module.exports = { preserveMetadata };
