import test from 'node:test';
import assert from 'node:assert/strict';
import {filterGames, candidatePool, randomIndex} from '../web/domain.js';

const now=1800000000;
const games=[
  {id:1,name:'甲',originalName:'First',minutes:0,lastPlayed:0,installed:true,groups:['a','b']},
  {id:2,name:'乙',originalName:'Second',minutes:60,lastPlayed:now-3*86400,installed:false,groups:['a']},
  {id:3,name:'丙',originalName:'Third',minutes:600,lastPlayed:now-200*86400,installed:true,groups:['b']},
];
const filters={search:'',groups:new Set(),installed:'all',playtime:'all',recent:'all'};
const ids=(changes={})=>filterGames(games,{...filters,...changes},now).map(g=>g.id);

test('multiple groups form a union without duplicate games',()=>assert.deepEqual(ids({groups:new Set(['a','b'])}),[1,2,3]));
test('search accepts translated name, original name and ID',()=>{
  assert.deepEqual(ids({search:'甲'}),[1]);assert.deepEqual(ids({search:'SECOND'}),[2]);assert.deepEqual(ids({search:'3'}),[3]);
});
test('installed, time and groups combine by intersection',()=>assert.deepEqual(ids({installed:'yes',playtime:'under2',groups:new Set(['b'])}),[1]));
test('recent and dormant filters never confuse missing records with old dates',()=>{
  assert.deepEqual(ids({recent:'old180'}),[3]);assert.deepEqual(ids({recent:'week'}),[2]);assert.deepEqual(ids({recent:'never'}),[1]);
});
test('duration boundary is exclusive for under10 and inclusive for over10',()=>{
  assert.deepEqual(ids({playtime:'under10'}),[1,2]);assert.deepEqual(ids({playtime:'over10'}),[3]);
});
test('manual selection remains intersected with current filters',()=>{
  assert.deepEqual(candidatePool(games.slice(0,2),new Set([1,3]),true,new Set(),true).map(g=>g.id),[1]);
  assert.equal(candidatePool(games,new Set(),true,new Set(),true).length,0);
});
test('no repeat exhausts, while disabling it restores the pool',()=>{
  assert.equal(candidatePool(games,new Set(),false,new Set([1,2,3]),true).length,0);
  assert.equal(candidatePool(games,new Set(),false,new Set([1,2,3]),false).length,3);
});
test('random rejects biased tail and never rolls an empty pool',()=>{
  const values=[0xffffffff,5];assert.equal(randomIndex(3,a=>{a[0]=values.shift();}),2);
  assert.throws(()=>randomIndex(0),RangeError);
});
