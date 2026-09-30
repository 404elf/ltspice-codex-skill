'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const { inspectLayout, estimateTextBox, loadSymbolWindows, placeSignalFlags, convertBest, readNet } = require('../scripts/weave_layout.js');

function rotate([x, y], code) {
  if (code[0] === 'M') x = -x;
  switch (Number(code.slice(1))) {
    case 0: return [x, y];
    case 90: return [-y, x];
    case 180: return [-x, -y];
    case 270: return [y, -x];
    default: throw new Error(`bad rotation ${code}`);
  }
}

const fixtureCore = {
  SYMBOLS: {
    box: { bbox: [-8, -4, 12, 6], pins: [[0, 0]] },
    res: { bbox: [0, 0, 32, 96], pins: [[16, 16], [16, 96]] },
  },
  rot: rotate,
};

function schematic(symbols, wires = []) {
  return ['Version 4', 'SHEET 1 1200 800',
    ...wires.map(w => `WIRE ${w.join(' ')}`),
    ...symbols.flatMap(s => [
      `SYMBOL ${s.sym || 'res'} ${s.x} ${s.y} ${s.rot || 'R0'}`,
      `SYMATTR InstName ${s.name}`,
      ...(s.value ? [`SYMATTR Value ${s.value}`] : []),
    ]), ''].join('\n');
}

async function main() {
  // All four transformed corners matter for asymmetric bodies under mirror+rotation.
  const asymCore = fixtureCore;
  const rotated = inspectLayout(schematic([{ sym: 'box', name: 'U1', x: 100, y: 200, rot: 'M90' }]), asymCore);
  assert.deepEqual(rotated.body_overlaps, []);
  assert.equal(rotated.unknown_symbols.length, 0);
  const transformed = inspectLayout(schematic([
    { sym: 'box', name: 'U1', x: 100, y: 200, rot: 'M90' },
    { sym: 'box', name: 'U2', x: 94, y: 188, rot: 'R0' },
  ]), asymCore);
  assert.deepEqual(transformed.body_overlaps[0].intersection, [94, 188, 104, 194]);

  const windowSymbol = { sym: 'box', name: 'U1', value: 'AB', x: 100, y: 200, rot: 'R90',
    windows: { 0: { x: 10, y: 20, align: 'Left', size: 2 } } };
  assert.deepEqual(estimateTextBox(windowSymbol, 'name', fixtureCore).bounds, [72, 210, 88, 226]);
  windowSymbol.rot = 'M90';
  assert.deepEqual(estimateTextBox(windowSymbol, 'name', fixtureCore).bounds, [72, 174, 88, 190]);

  // Edge contact has zero intersection area and is legal.
  const edgeTouch = inspectLayout(schematic([
    { sym: 'res', name: 'R1', x: 0, y: 0 },
    { sym: 'res', name: 'R2', x: 32, y: 0 },
  ]), fixtureCore);
  assert.deepEqual(edgeTouch.body_overlaps, []);

  const connectedFlag = inspectLayout(`${schematic([{ sym: 'res', name: 'R1', x: 0, y: 0 }])}FLAG 16 16 N_SIG\n`, fixtureCore);
  assert.ok(connectedFlag.estimated_text_conflicts.some(c => c.second === 'R1:body'));
  const flagTextWarning = inspectLayout(`${schematic([
    { sym: 'res', name: 'R1', x: 0, y: 0 }, { sym: 'res', name: 'R2', x: 64, y: 0 },
  ])}FLAG 16 16 N_SIGNAL\n`, fixtureCore);
  assert.ok(flagTextWarning.estimated_text_conflicts.length > 0);

  const crowded = `${schematic([{ sym: 'res', name: 'R1', x: 0, y: 0 }], [[-128, 16, 16, 16]])}FLAG 16 16 N_SIG\nFLAG 16 96 0\n`;
  let moveChecks = 0;
  const placed = placeSignalFlags(crowded, 'canonical NET', fixtureCore, (net, asc) => {
    assert.equal(net, 'canonical NET');
    assert.match(asc, /FLAG -\d+ 16 N_SIG/);
    moveChecks++;
    return [];
  });
  assert.equal(moveChecks, 1);
  assert.equal(placed.moves.length, 1);
  assert.deepEqual(inspectLayout(placed.asc, fixtureCore).estimated_text_conflicts, []);
  assert.deepEqual(placed.asc.split('\n').filter(l => !l.startsWith('FLAG')),
    crowded.split('\n').filter(l => !l.startsWith('FLAG')));
  assert.match(placed.asc, /FLAG 16 96 0/);
  assert.equal(placeSignalFlags(crowded, 'canonical NET', fixtureCore, () => ['changed connectivity']).asc, crowded);
  assert.equal(placeSignalFlags(`${schematic([{ sym: 'res', name: 'R1', x: 0, y: 0 }])}FLAG 16 16 N_SIG\n`,
    'canonical NET', fixtureCore, () => []).moves.length, 0);
  // A tempting point at a different wire crossing must not acquire the flag.
  const crossing = crowded.replace('WIRE -128 16 16 16', 'WIRE -128 16 16 16\nWIRE -48 -32 -48 64');
  const safePlacement = placeSignalFlags(crossing, 'canonical NET', fixtureCore, () => []);
  assert.equal(safePlacement.moves.length, 1);
  assert.notDeepEqual(safePlacement.moves[0].to, [-48, 16]);
  // A short pin stub and its contiguous collinear wire form the same route.
  const shortStub = crowded.replace('WIRE -128 16 16 16', 'WIRE 16 16 0 16\nWIRE 0 16 -128 16\nWIRE 0 16 -128 16');
  const stubPlacement = placeSignalFlags(shortStub, 'canonical NET', fixtureCore, () => []);
  assert.equal(stubPlacement.moves.length, 1);
  assert.deepEqual(inspectLayout(stubPlacement.asc, fixtureCore).estimated_text_conflicts, []);
  // A gap between collinear segments is not a connected route.
  const gap = crowded.replace('WIRE -128 16 16 16', 'WIRE 16 16 0 16\nWIRE -16 16 -128 16');
  assert.equal(placeSignalFlags(gap, 'canonical NET', fixtureCore, () => []).moves.length, 0);

  const unknown = inspectLayout(schematic([{ sym: '__missing', name: 'X1', x: 0, y: 0 }]), fixtureCore);
  assert.equal(unknown.unknown_symbols.length, 1);
  assert.equal(unknown.unknown_symbols[0].name, 'X1');
  for (const malformed of [
    schematic([{ sym: 'box', name: 'X2', x: NaN, y: 0 }]),
    schematic([{ sym: 'box', name: 'X3', x: 0, y: 0, rot: 'R45' }]),
  ]) assert.equal(inspectLayout(malformed, fixtureCore).unknown_symbols.length, 1);

  const asyRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'weave-symbol-windows-'));
  try {
    fs.writeFileSync(path.join(asyRoot, 'res.asy'), 'WINDOW 0 24 16 Left 2\nWINDOW 3 24 96 Left 2\n', 'latin1');
    fs.writeFileSync(path.join(asyRoot, 'box.asy'), Buffer.concat([Buffer.from([0xff, 0xfe]),
      Buffer.from('WINDOW 0 8 12 Left 2\nWINDOW 3 8 40 Right 2\n', 'utf16le')]));
    const coreWithAsy = { SYMBOLS: {
      res: { bbox: [0, 0, 32, 96], pins: fixtureCore.SYMBOLS.res.pins },
      box: { bbox: fixtureCore.SYMBOLS.box.bbox, pins: fixtureCore.SYMBOLS.box.pins },
    }, parseNetlist: () => ({ comps: [{ sym: 'res' }, { sym: 'box' }] }), rot: rotate };
    assert.deepEqual(loadSymbolWindows('fixture', coreWithAsy, [asyRoot]), []);
    assert.deepEqual(coreWithAsy.SYMBOLS.res.windows['0'], [24, 16, 'Left']);
    assert.deepEqual(coreWithAsy.SYMBOLS.box.windows['3'], [8, 40, 'Right']);
    const withAsy = inspectLayout(`${schematic([{ sym: 'res', name: 'R1', x: 0, y: 0, value: '1k' }])}`,
      coreWithAsy);
    assert.deepEqual(withAsy.unknown_text_geometry, []);
    const withoutAsy = { SYMBOLS: { res: { bbox: [0, 0, 32, 96] } }, rot: rotate };
    const fallback = inspectLayout(schematic([{ sym: 'res', name: 'R1', x: 0, y: 0, value: '1k' }]), withoutAsy);
    assert.equal(fallback.unknown_text_geometry.length, 2);
  } finally {
    fs.rmSync(asyRoot, { recursive: true, force: true });
  }

  if (process.env.WEAVE_CLI_DIR) {
    const actualCore = require(path.join(process.env.WEAVE_CLI_DIR, 'core.js'));
    assert.equal(inspectLayout(schematic([{ sym: 'res', name: 'R1', x: 0, y: 0 }]), actualCore).unknown_symbols.length, 0);
  }

  // Metadata is inserted before both connectivity and geometry checks. The
  // first mode's metadata introduces a body collision, so conversion retries.
  const order = [];
  const retryCore = {
    SYMBOLS: { box: { bbox: [0, 0, 32, 32] } },
    rot: rotate,
    parseNetlist: () => ({ comps: [] }),
    SAFE_MODES: [{ pass: 0 }, { pass: 1 }],
    async convert(_net, options) {
      order.push(`convert:${options.pass}`);
      return schematic([{ sym: 'box', name: 'A', x: 0, y: 0 }]);
    },
  };
  const metadata = (asc, _net, _core) => {
    const x = asc.includes('InstName A') && order.at(-1) === 'convert:0' ? 16 : 64;
    order.push(`metadata:${x}`);
    return { text: `${asc}SYMBOL box ${x} 0 R0\nSYMATTR InstName B\n` };
  };
  const compare = (_net, asc) => {
    order.push('compare');
    assert.match(asc, /SYMATTR InstName B/);
    return [];
  };
  const retried = await convertBest('fixture', { core: retryCore, compare, preserveMetadata: metadata });
  assert.equal(retried.status, 'PASS');
  assert.equal(retried.selected_mode, 1);
  assert.equal(retried.attempts[0].body_overlaps.length, 1);
  assert.deepEqual(order.slice(0, 3), ['convert:0', 'metadata:16', 'compare']);

  const failCore = { ...retryCore, SAFE_MODES: [{ pass: 0 }], async convert() {
    return schematic([{ sym: 'box', name: 'A', x: 0, y: 0 }]);
  } };
  const failed = await convertBest('fixture', { core: failCore, compare: () => [],
    preserveMetadata: asc => ({ text: `${asc}SYMBOL box 16 0 R0\nSYMATTR InstName B\n` }) });
  assert.equal(failed.status, 'FAIL');
  assert.equal(failed.asc, null);
  assert.equal(failed.selected_mode, null);
  assert.equal(failed.attempts[0].body_overlaps.length, 1);

  // End-to-end CLI fixture: a no-component NET succeeds, writes an ASC and
  // report, and leaves the source NET byte-for-byte unchanged.
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'weave-layout-'));
  try {
    const weaveDir = path.join(temp, 'weave');
    fs.mkdirSync(weaveDir);
    fs.writeFileSync(path.join(weaveDir, 'core.js'), [
      "module.exports = { SAFE_MODES: [{}], convert: async () => 'Version 4\\nSHEET 1 1200 800\\n',",
      '  parseNetlist: () => ({ comps: [] }), classifyNets: () => new Map(), SYMBOLS: {}, rot: p => p };',
    ].join('\n'));
    fs.writeFileSync(path.join(weaveDir, 'verify.js'), 'module.exports = { compare: () => [] };\n');
    const netPath = path.join(temp, 'fixture.net');
    const ascPath = path.join(temp, 'fixture.asc');
    const reportPath = path.join(temp, 'report.json');
    const netBytes = Buffer.from('\ufeffFixture\n.end\n', 'utf8');
    fs.writeFileSync(netPath, netBytes);
    const before = fs.readFileSync(netPath);
    const helper = path.resolve(__dirname, '../scripts/weave_layout.js');
    execFileSync(process.execPath, [helper, 'convert', weaveDir, netPath, ascPath, reportPath, '[]', '[]'], { encoding: 'utf8' });
    assert.deepEqual(fs.readFileSync(netPath), before);
    assert.equal(JSON.parse(fs.readFileSync(reportPath, 'utf8')).status, 'PASS');
    assert.match(fs.readFileSync(ascPath, 'utf8'), /Version 4/);

    const utf8BomPath = path.join(temp, 'bom.net');
    fs.writeFileSync(utf8BomPath, Buffer.from('\ufeffR1 a 0 1k\n', 'utf8'));
    assert.equal(readNet(utf8BomPath), 'R1 a 0 1k\n');
    const utf16Path = path.join(temp, 'utf16.net');
    fs.writeFileSync(utf16Path, Buffer.concat([Buffer.from([0xff, 0xfe]), Buffer.from('R1 a 0 1k\n', 'utf16le')]));
    assert.equal(readNet(utf16Path), 'R1 a 0 1k\n');
  } finally {
    fs.rmSync(temp, { recursive: true, force: true });
  }

  console.log('weave layout helper tests passed');
}

main().catch(error => { console.error(error); process.exitCode = 1; });
