#!/usr/bin/env node
'use strict';

// Weave's connectivity verifier does not check the visual layout. This bridge
// evaluates the final (metadata-preserved) ASC for every existing safe mode and
// only writes one when connectivity matches and known symbol bodies do not
// overlap.
const fs = require('fs');
const path = require('path');

function readNet(file) {
  const data = fs.readFileSync(file);
  if (data.length >= 2 && data[0] === 0xff && data[1] === 0xfe) {
    return data.subarray(2).toString('utf16le');
  }
  if (data.length >= 2 && data[0] === 0xfe && data[1] === 0xff) {
    const swapped = Buffer.alloc(data.length - 2);
    for (let i = 2; i + 1 < data.length; i += 2) {
      swapped[i - 2] = data[i + 1];
      swapped[i - 1] = data[i];
    }
    return swapped.toString('utf16le');
  }
  if (data.length >= 3 && data[0] === 0xef && data[1] === 0xbb && data[2] === 0xbf) {
    return data.subarray(3).toString('utf8');
  }
  // Canonical Skill NET files are UTF-8. Weave's CLI also treats a UTF-16LE
  // file without a BOM as UTF-16LE when its second byte is NUL.
  if (data.length > 1 && data[1] === 0) return data.toString('utf16le');
  return data.toString('utf8');
}

function parseAsc(text) {
  const syms = [];
  const wires = [];
  const flags = [];
  let current = null;
  for (const line of text.split(/\r?\n/)) {
    const fields = line.trim().split(/\s+/);
    if (fields[0] === 'SYMBOL' && fields.length >= 5) {
      current = { sym: fields[1], x: Number(fields[2]), y: Number(fields[3]), rot: fields[4], windows: {} };
      syms.push(current);
    } else if (fields[0] === 'SYMATTR' && current) {
      if (fields[1] === 'InstName') current.name = fields.slice(2).join(' ');
      if (fields[1] === 'Value') current.value = fields.slice(2).join(' ');
    } else if (fields[0] === 'WINDOW' && current && fields.length >= 5) {
      current.windows[fields[1]] = {
        x: Number(fields[2]), y: Number(fields[3]), align: fields[4], size: Number(fields[5] || 2),
      };
    } else if (fields[0] === 'WIRE' && fields.length >= 5) {
      const points = fields.slice(1, 5).map(Number);
      if (points.every(Number.isFinite)) wires.push(points);
    } else if (fields[0] === 'FLAG' && fields.length >= 4) {
      const x = Number(fields[1]), y = Number(fields[2]);
      if (Number.isFinite(x) && Number.isFinite(y)) flags.push({ x, y, name: fields.slice(3).join(' ') });
    }
  }
  return { syms, wires, flags };
}

function rotatedBody(symbol, core) {
  const entry = core.SYMBOLS[symbol.sym];
  if (!Number.isFinite(symbol.x) || !Number.isFinite(symbol.y)
      || !/^[RM](?:0|90|180|270)$/.test(symbol.rot)
      || !entry || !Array.isArray(entry.bbox) || entry.bbox.length !== 4
      || !entry.bbox.every(Number.isFinite) || typeof core.rot !== 'function') return null;
  const [x1, y1, x2, y2] = entry.bbox;
  let corners;
  try {
    corners = [[x1, y1], [x1, y2], [x2, y1], [x2, y2]]
      .map(point => core.rot(point, symbol.rot));
  } catch (_) {
    return null;
  }
  if (corners.some(point => !Array.isArray(point) || !point.every(Number.isFinite))) return null;
  const bounds = [
    symbol.x + Math.min(...corners.map(point => point[0])),
    symbol.y + Math.min(...corners.map(point => point[1])),
    symbol.x + Math.max(...corners.map(point => point[0])),
    symbol.y + Math.max(...corners.map(point => point[1])),
  ];
  return bounds.every(Number.isFinite) ? bounds : null;
}

function positiveAreaOverlap(a, b) {
  return Math.max(a[0], b[0]) < Math.min(a[2], b[2])
    && Math.max(a[1], b[1]) < Math.min(a[3], b[3]);
}

// Only report a wire/body warning if a segment enters the open interior. A
// wire ending at a pin on the symbol boundary is an ordinary connection.
function wireEntersBody(wire, body) {
  const [x1, y1, x2, y2] = wire;
  if (y1 === y2) {
    return body[1] < y1 && y1 < body[3]
      && Math.max(Math.min(x1, x2), body[0]) < Math.min(Math.max(x1, x2), body[2]);
  }
  if (x1 === x2) {
    return body[0] < x1 && x1 < body[2]
      && Math.max(Math.min(y1, y2), body[1]) < Math.min(Math.max(y1, y2), body[3]);
  }
  return false;
}

function estimateTextBox(symbol, kind, core) {
  const text = kind === 'name' ? symbol.name : symbol.value;
  if (!text) return null;
  const supplied = symbol.windows[kind === 'name' ? '0' : '3'];
  const defaults = core.SYMBOLS[symbol.sym]?.windows || {};
  const windowKey = kind === 'name' ? '0' : '3';
  const fallback = defaults[windowKey]
    || (kind === 'name' ? [36, 40, 'Left'] : [36, 76, 'Left']);
  const wx = supplied ? supplied.x : fallback[0];
  const wy = supplied ? supplied.y : fallback[1];
  const align = supplied ? supplied.align : fallback[2];
  const size = supplied ? supplied.size : 2;
  // LTspice character metrics are not encoded in ASC. These deliberately
  // conservative boxes are estimates for warning/ranking only.
  const width = Math.max(16, Math.min(String(text).length, 64) * 8 * Math.max(1, size / 2));
  const height = 16 * Math.max(1, size / 2);
  const direction = String(align).toLowerCase();
  let local;
  if (direction === 'right') local = [wx - width, wy - height / 2, wx, wy + height / 2];
  else if (direction === 'center' || direction === 'middle') local = [wx - width / 2, wy - height / 2, wx + width / 2, wy + height / 2];
  else if (direction === 'top') local = [wx - width / 2, wy, wx + width / 2, wy + height];
  else if (direction === 'bottom') local = [wx - width / 2, wy - height, wx + width / 2, wy];
  else if (direction === 'vtop') local = [wx, wy - width / 2, wx + height, wy + width / 2];
  else if (direction === 'vbottom') local = [wx - height, wy - width / 2, wx, wy + width / 2];
  else if (direction === 'vleft') local = [wx - height / 2, wy - width, wx + height / 2, wy];
  else if (direction === 'vright') local = [wx - height / 2, wy, wx + height / 2, wy + width];
  else local = [wx, wy - height / 2, wx + width, wy + height / 2];
  const [x1, y1, x2, y2] = local;
  let corners;
  try {
    corners = [[x1, y1], [x1, y2], [x2, y1], [x2, y2]]
      .map(point => core.rot(point, symbol.rot));
  } catch (_) {
    return null;
  }
  if (corners.some(point => !Array.isArray(point) || !point.every(Number.isFinite))) return null;
  return {
    owner: symbol.name || symbol.sym,
    kind,
    confidence: 'estimated',
    geometry_known: Boolean(supplied || defaults[windowKey]),
    bounds: [symbol.x + Math.min(...corners.map(point => point[0])),
      symbol.y + Math.min(...corners.map(point => point[1])),
      symbol.x + Math.max(...corners.map(point => point[0])),
      symbol.y + Math.max(...corners.map(point => point[1]))],
  };
}

function loadSymbolWindows(netText, core, symbolRoots = []) {
  const comps = core.parseNetlist(netText).comps;
  const missing = new Set();
  for (const sym of new Set(comps.map(comp => comp.sym))) {
    const entry = core.SYMBOLS[sym];
    if (!entry) continue;
    const windows = { ...(entry.windows || {}) };
    const relative = `${sym.replace(/[\\/]/g, path.sep)}.asy`;
    for (const root of symbolRoots) {
      const file = path.resolve(root, relative);
      if (!fs.existsSync(file)) continue;
      const bytes = fs.readFileSync(file);
      let text;
      if (bytes.length >= 2 && bytes[0] === 0xff && bytes[1] === 0xfe) text = bytes.subarray(2).toString('utf16le');
      else if (bytes.length >= 3 && bytes[0] === 0xef && bytes[1] === 0xbb && bytes[2] === 0xbf) text = bytes.subarray(3).toString('utf8');
      else text = bytes.toString('latin1');
      for (const line of text.split(/\r?\n/)) {
        const match = line.match(/^\s*WINDOW\s+(0|3)\s+(-?\d+)\s+(-?\d+)\s+(\S+)(?:\s+\d+)?\s*$/i);
        if (match) windows[match[1]] = [Number(match[2]), Number(match[3]), match[4]];
      }
      if (windows['0'] || windows['3']) break;
    }
    if (Object.keys(windows).length) entry.windows = windows;
    if (!(windows['0'] && windows['3'])) missing.add(sym);
  }
  return [...missing].sort();
}

function inspectLayout(ascText, core) {
  const { syms, wires, flags } = parseAsc(ascText);
  const bodies = [];
  const unknown_symbols = [];
  const unknown_text_geometry = [];
  const body_overlaps = [];
  const wire_body_conflicts = [];
  const text_boxes = [];
  for (const symbol of syms) {
    const bounds = rotatedBody(symbol, core);
    if (!bounds) {
      unknown_symbols.push({ name: symbol.name || null, symbol: symbol.sym, rotation: symbol.rot });
      continue;
    }
    bodies.push({ name: symbol.name || symbol.sym, symbol: symbol.sym, bounds });
    for (const kind of ['name', 'value']) {
      const box = estimateTextBox(symbol, kind, core);
      if (box) text_boxes.push(box);
      if (box && !box.geometry_known) {
        unknown_text_geometry.push({ name: symbol.name || null, symbol: symbol.sym, kind,
          fallback: 'generic estimated WINDOW position' });
      }
    }
  }
  for (const flag of flags) {
    // FLAG 0 renders the ground glyph, not a node-name text label.
    if (flag.name === '0') continue;
    const width = Math.max(16, Math.min(flag.name.length, 64) * 8);
    text_boxes.push({ owner: `FLAG:${flag.name}@${flag.x},${flag.y}`, kind: 'flag',
      anchor: [flag.x, flag.y], confidence: 'estimated',
      bounds: [flag.x + 8, flag.y - 8, flag.x + 8 + width, flag.y + 8] });
  }
  for (let i = 0; i < bodies.length; i++) {
    for (let j = i + 1; j < bodies.length; j++) {
      if (positiveAreaOverlap(bodies[i].bounds, bodies[j].bounds)) {
        body_overlaps.push({ first: bodies[i].name, second: bodies[j].name,
          intersection: [Math.max(bodies[i].bounds[0], bodies[j].bounds[0]),
            Math.max(bodies[i].bounds[1], bodies[j].bounds[1]),
            Math.min(bodies[i].bounds[2], bodies[j].bounds[2]),
            Math.min(bodies[i].bounds[3], bodies[j].bounds[3])] });
      }
    }
  }
  for (const body of bodies) {
    for (let i = 0; i < wires.length; i++) {
      if (wireEntersBody(wires[i], body.bounds)) {
        wire_body_conflicts.push({ component: body.name, wire_index: i, wire: wires[i] });
      }
    }
  }
  const estimated_text_conflicts = [];
  for (let i = 0; i < text_boxes.length; i++) {
    for (let j = i + 1; j < text_boxes.length; j++) {
      if (text_boxes[i].owner !== text_boxes[j].owner
          && positiveAreaOverlap(text_boxes[i].bounds, text_boxes[j].bounds)) {
        estimated_text_conflicts.push({ first: `${text_boxes[i].owner}:${text_boxes[i].kind}`,
          second: `${text_boxes[j].owner}:${text_boxes[j].kind}`, confidence: 'estimated' });
      }
    }
    for (const body of bodies) {
      // A flag connected to a pin can still cover that component's body.
      if (body.name !== text_boxes[i].owner
          && positiveAreaOverlap(text_boxes[i].bounds, body.bounds)) {
        estimated_text_conflicts.push({ first: `${text_boxes[i].owner}:${text_boxes[i].kind}`,
          second: `${body.name}:body`, confidence: 'estimated' });
      }
    }
  }
  const points = bodies.map(body => body.bounds);
  const minX = points.length ? Math.min(...points.map(b => b[0])) : 0;
  const minY = points.length ? Math.min(...points.map(b => b[1])) : 0;
  const maxX = points.length ? Math.max(...points.map(b => b[2])) : 0;
  const maxY = points.length ? Math.max(...points.map(b => b[3])) : 0;
  return {
    body_overlaps,
    unknown_symbols,
    unknown_text_geometry,
    wire_body_conflicts,
    estimated_text_conflicts,
    text_estimate_confidence: 'estimated',
    area: Math.max(0, maxX - minX) * Math.max(0, maxY - minY),
  };
}

function pointOnWire(x, y, wire) {
  const [x1, y1, x2, y2] = wire;
  return (x1 === x2 && x === x1 && Math.min(y1, y2) <= y && y <= Math.max(y1, y2))
    || (y1 === y2 && y === y1 && Math.min(x1, x2) <= x && x <= Math.max(x1, x2));
}

// Move only node-name anchors along their original connected straight wires. No wire,
// symbol, ground or other ASC line changes; each accepted move must still MATCH.
function placeSignalFlags(ascText, netText, core, compare) {
  let asc = ascText;
  let conflicts = inspectLayout(asc, core).estimated_text_conflicts.length;
  const moves = [];
  const parsed = parseAsc(ascText);
  const lines = ascText.split(/\r?\n/);
  const flagLines = [];
  lines.forEach((line, index) => {
    const fields = line.trim().split(/\s+/);
    if (fields[0] === 'FLAG' && fields.length >= 4
        && Number.isFinite(Number(fields[1])) && Number.isFinite(Number(fields[2]))) flagLines.push(index);
  });
  const pins = parsed.syms.flatMap(symbol => (core.SYMBOLS[symbol.sym]?.pins || []).map(pin => {
    const [x, y] = core.rot(pin, symbol.rot);
    return [symbol.x + x, symbol.y + y];
  }));
  for (let flagIndex = 0; flagIndex < parsed.flags.length && conflicts; flagIndex++) {
    const flag = parsed.flags[flagIndex];
    if (flag.name === '0') continue;
    const candidates = new Map();
    parsed.wires.forEach((wire, wireIndex) => {
      if (!pointOnWire(flag.x, flag.y, wire)) return;
      const horizontal = wire[1] === wire[3];
      if (wire[0] === wire[2] && horizontal) return;
      let low = Math.min(horizontal ? wire[0] : wire[1], horizontal ? wire[2] : wire[3]);
      let high = Math.max(horizontal ? wire[0] : wire[1], horizontal ? wire[2] : wire[3]);
      const chain = new Set([wireIndex]);
      // Weave inserts short pin stubs. Follow contiguous collinear segments,
      // including duplicate/overlapping records, without crossing a bend.
      let expanded;
      do {
        expanded = false;
        parsed.wires.forEach((other, index) => {
          if (chain.has(index)) return;
          const sameLine = horizontal ? other[1] === wire[1] && other[3] === wire[1]
            : other[0] === wire[0] && other[2] === wire[0];
          if (!sameLine) return;
          const start = Math.min(horizontal ? other[0] : other[1], horizontal ? other[2] : other[3]);
          const end = Math.max(horizontal ? other[0] : other[1], horizontal ? other[2] : other[3]);
          if (end < low || start > high) return;
          chain.add(index);
          low = Math.min(low, start);
          high = Math.max(high, end);
          expanded = true;
        });
      } while (expanded);
      // Bounded to 32 grid steps in either direction, nearest positions first.
      for (let step = 1; step <= 32; step++) {
        for (const direction of [-1, 1]) {
          const x = flag.x + (horizontal ? direction * step * 16 : 0);
          const y = flag.y + (horizontal ? 0 : direction * step * 16);
          const along = horizontal ? x : y;
          if (along < low || along > high) continue;
          // Avoid creating a junction at a crossing or another component pin.
          if (parsed.wires.some((other, i) => !chain.has(i) && pointOnWire(x, y, other))
              || pins.some(pin => pin[0] === x && pin[1] === y)) continue;
          candidates.set(`${x},${y}`, { x, y, distance: step * 16 });
        }
      }
    });
    let best = null;
    for (const point of [...candidates.values()].sort((a, b) => a.distance - b.distance || a.x - b.x || a.y - b.y)) {
      const trialLines = [...lines];
      trialLines[flagLines[flagIndex]] = `FLAG ${point.x} ${point.y} ${flag.name}`;
      const trial = trialLines.join(ascText.includes('\r\n') ? '\r\n' : '\n');
      const count = inspectLayout(trial, core).estimated_text_conflicts.length;
      if (count < conflicts && (!best || count < best.count)) {
        best = { ...point, count, trial };
        if (!count) break;
      }
    }
    if (best && compare(netText, best.trial).length === 0) {
      lines[flagLines[flagIndex]] = `FLAG ${best.x} ${best.y} ${flag.name}`;
      asc = best.trial;
      conflicts = best.count;
      moves.push({ name: flag.name, from: [flag.x, flag.y], to: [best.x, best.y], connectivity_checked: true });
    }
  }
  return { asc, moves };
}

function score(layout) {
  return [layout.body_overlaps.length, layout.estimated_text_conflicts.length,
    layout.wire_body_conflicts.length, layout.area];
}

function compareScore(a, b) {
  const x = score(a), y = score(b);
  for (let i = 0; i < x.length; i++) if (x[i] !== y[i]) return x[i] - y[i];
  return 0;
}

async function convertBest(netText, { core, compare, preserveMetadata, explicitSubcircuits = [], symbolRoots = [] }) {
  const attempts = [];
  let bestAccepted = null;
  const modes = core.SAFE_MODES || [{}];
  loadSymbolWindows(netText, core, symbolRoots);
  for (let mode = 0; mode < modes.length; mode++) {
    const options = modes[mode];
    const attempt = { mode, options, connectivity_errors: [], body_overlaps: [], unknown_symbols: [], unknown_text_geometry: [],
      estimated_text_conflicts: [], wire_body_conflicts: [], label_moves: [], area: null };
    try {
      const generated = await core.convert(netText, options);
      const metadata = preserveMetadata(generated, netText, core, explicitSubcircuits);
      let asc = typeof metadata === 'string' ? metadata : metadata.text;
      attempt.connectivity_errors = compare(netText, asc);
      let layout = inspectLayout(asc, core);
      if (!attempt.connectivity_errors.length && !layout.unknown_symbols.length && !layout.body_overlaps.length) {
        const placed = placeSignalFlags(asc, netText, core, compare);
        asc = placed.asc;
        attempt.label_moves = placed.moves;
        layout = inspectLayout(asc, core);
        attempt.connectivity_errors = compare(netText, asc);
      }
      Object.assign(attempt, layout);
      attempts.push(attempt);
      if (!attempt.connectivity_errors.length && !layout.unknown_symbols.length
          && !layout.body_overlaps.length) {
        if (!bestAccepted || compareScore(layout, bestAccepted.layout) < 0) {
          bestAccepted = { mode, options, asc, layout, attempt };
        }
      }
    } catch (error) {
      attempt.error = String(error && error.message || error);
      attempts.push(attempt);
    }
  }
  if (!bestAccepted) {
    const candidates = attempts.filter(a => Array.isArray(a.body_overlaps) && Number.isFinite(a.area));
    const best = candidates.sort(compareScore)[0] || null;
    return {
      status: 'FAIL', selected_mode: null, attempts, selected: null,
      best_rejected: best ? { mode: best.mode, body_overlaps: best.body_overlaps,
        unknown_symbols: best.unknown_symbols, unknown_text_geometry: best.unknown_text_geometry,
        estimated_text_conflicts: best.estimated_text_conflicts,
        wire_body_conflicts: best.wire_body_conflicts, connectivity_errors: best.connectivity_errors } : null,
      warnings: ['No candidate passed connectivity, known-symbol geometry, and zero-body-overlap checks.'],
      asc: null,
    };
  }
  const layout = bestAccepted.layout;
  return {
    status: 'PASS', selected_mode: bestAccepted.mode, attempts,
    selected: { mode: bestAccepted.mode, options: bestAccepted.options,
      label_moves: bestAccepted.attempt.label_moves,
      body_overlaps: layout.body_overlaps, unknown_symbols: layout.unknown_symbols,
      unknown_text_geometry: layout.unknown_text_geometry,
      estimated_text_conflicts: layout.estimated_text_conflicts,
      wire_body_conflicts: layout.wire_body_conflicts, area: layout.area },
    warnings: [
      ...(layout.estimated_text_conflicts.length ? ['Estimated text collisions remain; text boxes are approximate.'] : []),
      ...(layout.unknown_text_geometry.length ? ['Some symbol text WINDOW positions use generic estimates because matching .asy files were unavailable.'] : []),
      ...(layout.wire_body_conflicts.length ? ['Wire segments enter symbol body bounds; pin contact is excluded.'] : []),
    ],
    asc: bestAccepted.asc,
  };
}

async function cli(argv) {
  const [command, weaveDir, netPath, ascPath, reportPath, subcircuitsJson = '[]', symbolRootsJson = '[]'] = argv;
  if (command !== 'convert' || !weaveDir || !netPath || !ascPath || !reportPath) {
    throw new Error('usage: node weave_layout.js convert <weaveDir> <netPath> <ascPath> <reportPath> <explicitSubcircuitsJSON>');
  }
  const netText = readNet(netPath);
  const core = require(path.resolve(weaveDir, 'core.js'));
  const { compare } = require(path.resolve(weaveDir, 'verify.js'));
  const { preserveMetadata } = require(path.join(__dirname, 'weave_metadata.js'));
  const explicit = JSON.parse(subcircuitsJson);
  const symbolRoots = JSON.parse(symbolRootsJson);
  const result = await convertBest(netText, { core, compare, preserveMetadata,
    explicitSubcircuits: explicit, symbolRoots });
  if (result.status === 'PASS') fs.writeFileSync(ascPath, Buffer.from(result.asc, 'latin1'));
  delete result.asc;
  fs.writeFileSync(reportPath, JSON.stringify(result, null, 2) + '\n', 'utf8');
  process.stdout.write(JSON.stringify({ status: result.status, selected_mode: result.selected_mode, report: reportPath }) + '\n');
  return result.status === 'PASS' ? 0 : 1;
}

if (require.main === module) {
  cli(process.argv.slice(2)).then(code => { process.exitCode = code; }).catch(error => {
    const argv = process.argv.slice(2);
    const reportPath = argv[4];
    const report = { status: 'FAIL', selected_mode: null, attempts: [], selected: null,
      warnings: [String(error && error.message || error)] };
    if (reportPath) {
      try { fs.writeFileSync(reportPath, JSON.stringify(report, null, 2) + '\n', 'utf8'); } catch (_) { /* report path itself may be invalid */ }
    }
    process.stderr.write(`${JSON.stringify(report)}\n`);
    process.exitCode = 1;
  });
}

module.exports = { inspectLayout, estimateTextBox, loadSymbolWindows, placeSignalFlags, convertBest, parseAsc, readNet, cli };
