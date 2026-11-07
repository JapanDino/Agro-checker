const test=require('node:test');const assert=require('node:assert/strict');
const {displayValue,wireValue}=require('../web/validation/plan-editor.js');
test('Editable percentages round-trip as fractions without turning empty limits into zero',()=>{
  assert.equal(displayValue(.95,'fraction'),'95');assert.equal(wireValue('95','fraction'),.95);
  assert.equal(displayValue(null,'ч'),'');assert.equal(wireValue('','ч'),null);assert.equal(wireValue('0','случаев'),0);
  assert.equal(displayValue(.125,'fraction'),'12.5');assert.equal(wireValue('-0.1','°C'),-.1);
  assert.throws(()=>wireValue('bad','fraction'));
});
