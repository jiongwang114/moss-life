// Private JSON-lines worker. No listening port, database credentials, or Obsidian dependency.
import readline from 'node:readline';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {readFileSync} from 'node:fs';
import {mkdir} from 'node:fs/promises';
import {SmartEmbedTransformersAdapter} from './vendor/jsbrains/smart-embed-model/adapters/transformers.js';
import {cos_sim} from './vendor/jsbrains/smart-utils/cos_sim.js';
import {results_acc} from './vendor/jsbrains/smart-utils/results_acc.js';
import {CollectionEngine} from './collection-engine.mjs';

// Upstream diagnostics must never corrupt the machine-readable stdout protocol.
console.log = (...args) => console.error(...args);
console.time=()=>{};
console.timeEnd=()=>{};

export class ServerEmbeddingAdapter extends SmartEmbedTransformersAdapter {
  async load() {
    if (this.pipeline && this.tokenizer) return;
    const {pipeline, AutoTokenizer, env} = await import('@huggingface/transformers');
    const cacheDir = path.resolve(process.env.MOSS_SEARCH_CACHE || '.smart-env/models');
    await mkdir(cacheDir, {recursive:true});
    env.cacheDir = cacheDir;
    env.allowLocalModels = true;
    const localModel=process.env.MOSS_SEARCH_MODEL_DIR;
    if(localModel){
      const manifest=JSON.parse(readFileSync(path.join(localModel,'moss-model-manifest.json'),'utf8'));
      if(manifest.model!==this.model_key || manifest.revision!==this.model.data.revision)throw Error('MODEL_PROFILE_MISMATCH');
    }
    const source=localModel ? path.resolve(localModel) : this.model_key;
    const threads=Math.max(1,Number(process.env.MOSS_SEARCH_THREADS)||1);
    const options = {device:'cpu', dtype:'q8', revision:this.model.data.revision, local_files_only:Boolean(localModel),
      session_options:{intraOpNumThreads:threads,interOpNumThreads:1}};
    this.pipeline = await pipeline('feature-extraction', source, options);
    this.tokenizer = this.pipeline.tokenizer || await AutoTokenizer.from_pretrained(source, {revision:options.revision,local_files_only:options.local_files_only});
    this.has_gpu = false;
    this.loaded = true;
  }
}

export function rank(queryVector, entries, limit) {
  const acc={results:new Set(),min:Infinity,minResult:null};
  for (const entry of entries) {
    const score=cos_sim(queryVector,entry.vector);
    if (!Number.isFinite(score)) throw Error('INVALID_SCORE');
    results_acc(acc,{id:entry.id,score},limit);
  }
  return [...acc.results].sort((a,b)=>b.score-a.score || a.id.localeCompare(b.id));
}

let adapter;
let engine;
const profiles=JSON.parse(readFileSync(new URL('./model-profiles.json',import.meta.url),'utf8'));
const profile=profiles.profiles[process.env.MOSS_SEARCH_PROFILE||profiles.default];
async function model() {
  if (!adapter) {
    const data={max_tokens:256,batch_size:4,revision:profile.revision,pooling:profile.pooling,query_prefix:profile.query_prefix};
    adapter=new ServerEmbeddingAdapter({data,model_key:profile.model,settings:{}});
  }
  await adapter.load();
  return adapter;
}

async function collectionEngine(){
  if(!engine){
    const adapter=await model();
    // Present the model interface expected by the original queue, without requiring Obsidian.
    adapter.model.data.max_tokens=256;
    Object.defineProperty(adapter,'is_loaded',{get:()=>Boolean(adapter.pipeline&&adapter.tokenizer)});
    engine=new CollectionEngine(path.resolve(process.env.MOSS_SEARCH_DIRECTORY || '.smart-env'),adapter,splitDocument);
  }
  return engine;
}

// Token-aware slicing covers the whole body, including long Chinese notes and emoji.
export async function splitDocument(m, {title='',tags=[],body='',include_tags=true}) {
  let prefix=`标题：${title}\n${include_tags ? `标签：${tags.join('；')}\n` : ''}`;
  while ((await m.count_tokens(prefix)).tokens > 80) prefix=prefix.slice(0,Math.floor(prefix.length*.8));
  const chunks=[];
  let start=0;
  while (start<body.length) {
    let low=start+1,high=Math.min(body.length,start+1600),end=start;
    while(low<=high) {
      const middle=Math.floor((low+high)/2);
      if ((await m.count_tokens(prefix+body.slice(start,middle))).tokens<=m.max_tokens) {end=middle;low=middle+1;} else high=middle-1;
    }
    if(end===start) throw Error('CHUNK_BUDGET_TOO_SMALL');
    chunks.push({text:prefix+body.slice(start,end),snippet:body.slice(start,end),start,end});
    if(end===body.length) break;
    start=end-Math.min(40,Math.floor((end-start)/5));
  }
  return chunks;
}

async function execute(message) {
  if(message.op==='rank') return rank(message.vector,message.entries,message.limit);
  if(message.op==='diagnostics')return {rss_bytes:process.memoryUsage().rss};
  if(message.op==='upsert')return await (await collectionEngine()).upsert(message);
  if(message.op==='inventory')return await (await collectionEngine()).inventory();
  if(message.op==='remove'){await (await collectionEngine()).remove(message.record_id);return {removed:true}}
  if(message.op==='lookup')return await (await collectionEngine()).search(message);
  const m=await model();
  if(message.op==='ready') return {model:m.model_key};
  if(message.op==='query') {
    if((await m.count_tokens(message.text)).tokens>m.max_tokens) throw Error('QUERY_TOO_LONG');
    return (await m.embed(message.text)).vec;
  }
  if(message.op==='document') {
    const chunks=await splitDocument(m,message);
    const embedded=await m.embed_batch(chunks.map(c=>({embed_input:c.text})));
    return chunks.map((chunk,i)=>({...chunk,vector:embedded[i].vec}));
  }
  throw Error('UNKNOWN_OPERATION');
}

if(process.argv[1] && path.resolve(process.argv[1])===path.resolve(fileURLToPath(import.meta.url))) {
  const lines=readline.createInterface({input:process.stdin,crlfDelay:Infinity});
  for await (const line of lines) {
    let message;
    try {
      message=JSON.parse(line);
      const result=await execute(message);
      process.stdout.write(JSON.stringify({id:message.id,result})+'\n');
    } catch(error) {
      // No record content or raw upstream errors in the protocol/logs.
      const code=['QUERY_TOO_LONG','CHUNK_BUDGET_TOO_SMALL','UNKNOWN_OPERATION'].includes(error.message)?error.message:'MODEL_OR_WORKER_FAILED';
      process.stderr.write(`smart-search: ${code}\n`);
      process.stdout.write(JSON.stringify({id:message?.id,error:code})+'\n');
    }
  }
  await adapter?.unload();
}
