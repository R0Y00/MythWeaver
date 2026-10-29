// Exercise actual frontend source with secure-context crypto APIs absent.
import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import vm from 'node:vm';

const require=createRequire(new URL('../apps/web/package.json',import.meta.url));
const ts=require('typescript');
const root=new URL('../apps/web/src/',import.meta.url);
function compile(name){
  return ts.transpileModule(readFileSync(new URL(name,root),'utf8'),{
    compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS},fileName:name,
  }).outputText;
}
function identifiers(crypto){
  const context=vm.createContext({exports:{},crypto});
  vm.runInContext(compile('request-id.ts'),context);
  return context.exports.requestId;
}
const pattern=/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
for(const crypto of [{getRandomValues:values=>webcrypto.getRandomValues(values)},undefined]){
  const requestId=identifiers(crypto),ids=new Set();
  for(let index=0;index<1000;index++){
    const id=requestId();assert.match(id,pattern);ids.add(id);
  }
  assert.equal(ids.size,1000);
}
const secure=identifiers({randomUUID:()=> '11111111-1111-4111-8111-111111111111'});
assert.equal(secure(),'11111111-1111-4111-8111-111111111111');
const calls=[],requestId=identifiers({getRandomValues:values=>webcrypto.getRandomValues(values)});
const context=vm.createContext({exports:{},require:name=>name==='react'?{}:{requestId},fetch:async(url,options)=>{
  calls.push({url,options});return {ok:true,status:200,json:async()=>({role:'admin'})};
}});
vm.runInContext(compile('api.ts'),context);
await context.exports.api('/login','POST',{password:'dummy-test-password'});
assert.equal(calls.length,1);
assert.equal(calls[0].url,'/api/login');
assert.match(calls[0].options.headers['Idempotency-Key'],pattern);
assert.equal(calls[0].options.headers['X-MythWeaver'],'1');
assert.equal(JSON.parse(calls[0].options.body).password,'dummy-test-password');
const source=readFileSync(new URL('pages/management.tsx',root),'utf8');
assert.ok(source.includes('key:requestId()'));
assert.ok(!source.includes('crypto.randomUUID()'));
console.log('Passed: HTTPS UUID, HTTP getRandomValues fallback, legacy fallback uniqueness, HTTP login reaches fetch, player request IDs.');
