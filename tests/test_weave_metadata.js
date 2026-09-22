'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { preserveMetadata } = require('../scripts/weave_metadata.js');

function fixture(comps) {
  return {
    parseNetlist: () => ({ comps }),
    classifyNets: () => new Map(comps.flatMap(c => c.nets.map(n => [n, n === '0' ? 'gnd' : 'signal']))),
    SYMBOLS: { res: { pins: [[16, 16], [16, 96]] }, amp: { pins: [[-32, 80], [-32, 48], [0, 32], [0, 96], [32, 64]] } },
    rot: ([x, y], code) => code === 'R90' ? [-y, x] : [x, y],
  };
}

test('signal names attach to rotated pins without changing wires or symbols', () => {
  const core = fixture([{ name: 'R1', sym: 'res', nets: ['sensor', 'out'], value: '10k' }]);
  const asc = 'Version 4\nWIRE 0 0 32 0\nSYMBOL res 200 100 R90\nSYMATTR InstName R1\nSYMATTR Value 10k\n';
  const result = preserveMetadata(asc, '', core);
  assert.match(result.text, /^FLAG 184 116 sensor$/m);
  assert.match(result.text, /^FLAG 104 116 out$/m);
  assert.equal(result.text.replace(/^FLAG .*\n/gm, ''), asc);
  assert.equal(result.labelsAdded, 2);
  assert.equal(preserveMetadata(result.text, '', core).text, result.text);
});

test('only one label per signal, and existing ground stays intact', () => {
  const core = fixture([
    { name: 'R1', sym: 'res', nets: ['out', '0'], value: '10k' },
    { name: 'R2', sym: 'res', nets: ['out', '0'], value: '20k' },
  ]);
  const asc = 'FLAG 16 96 0\nSYMBOL res 0 0 R0\nSYMATTR InstName R1\nSYMBOL res 200 0 R0\nSYMATTR InstName R2\n';
  const result = preserveMetadata(asc, '', core);
  assert.equal(result.labelsAdded, 1);
  assert.match(result.text, /^FLAG 16 96 0$/m);
});

test('explicit readable model overrides inherited model name and automatic library', () => {
  const core = fixture([{ name: 'XU1', sym: 'amp', nets: ['p', 'm', 'vp', 'vm', 'out'], value: 'DEVICE_CUSTOM gain=2' }]);
  const asc = 'SYMBOL amp 100 200 R0\nSYMATTR InstName XU1\nSYMATTR Value DEVICE_CUSTOM gain=2\nSYMATTR Value2 WRONG\nSYMATTR SpiceModel wrong.lib\n';
  const result = preserveMetadata(asc, '', core, ['device_custom']);
  assert.match(result.text, /^SYMATTR Value2 DEVICE_CUSTOM gain=2$/m);
  assert.match(result.text, /^SYMATTR SpiceModel ""$/m);
  assert.doesNotMatch(result.text, /WRONG|wrong.lib/);
  assert.equal(result.modelOverrides, 1);
});

test('automatic library is preserved when canonical NET has no readable definition', () => {
  const core = fixture([{ name: 'XU1', sym: 'amp', nets: ['p', 'm', 'vp', 'vm', 'out'], value: 'DEVICE' }]);
  const asc = 'SYMBOL amp 0 0 R0\nSYMATTR InstName XU1\nSYMATTR SpiceModel builtin.lib\n';
  const result = preserveMetadata(asc, '', core, ['UNRELATED']);
  assert.match(result.text, /^SYMATTR SpiceModel builtin.lib$/m);
  assert.equal(result.modelOverrides, 0);
});

test('missing or differently mapped symbol is rejected', () => {
  const core = fixture([{ name: 'R1', sym: 'res', nets: ['out', '0'], value: '10k' }]);
  assert.throws(() => preserveMetadata('Version 4\n', '', core), /missing NET instances/);
  assert.throws(() => preserveMetadata('SYMBOL amp 0 0 R0\nSYMATTR InstName R1\n', '', core), /cannot bind/);
});

test('conflicting ground label cannot rename a signal', () => {
  const core = fixture([{ name: 'R1', sym: 'res', nets: ['out', '0'], value: '10k' }]);
  assert.throws(() => preserveMetadata('FLAG 16 16 0\nSYMBOL res 0 0 R0\nSYMATTR InstName R1\n', '', core), /conflicting node labels/);
});
