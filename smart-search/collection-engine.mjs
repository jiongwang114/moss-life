// Database-to-collection bridge. Vector queues, vector state, persistence and nearest
// retrieval are delegated to the original MIT Smart Entities / Smart Collections code.
import path from 'node:path';
import {readdir, mkdir, unlink} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {SmartEntities} from './vendor/jsbrains/smart-entities/smart_entities.js';
import {SmartEntity} from './vendor/jsbrains/smart-entities/smart_entity.js';
import {AjsonMultiFileCollectionDataAdapter} from './vendor/jsbrains/smart-collections/adapters/ajson_multi_file.js';
import {NodeFsSmartFsAdapter} from './vendor/jsbrains/smart-fs/adapters/node_fs.js';

class RecordChunk extends SmartEntity {
  get collection_key(){return 'smart_entities'}
  get should_embed(){return Boolean(this.data.text) && !this.deleted}
  get size(){return this.data.text?.length || 0}
  async get_embed_input(){this._embed_input=this.data.text;return this._embed_input}
  queue_import(){this._queue_embed=true}
  filter(options={}) {
    if(this.deleted)return false;
    if(options.moss_user && this.data.user_id!==options.moss_user)return false;
    if(options.moss_allowed?.[this.data.record_id]!==undefined && options.moss_allowed[this.data.record_id]!==this.data.fingerprint)return false;
    if(options.moss_allowed && !Object.hasOwn(options.moss_allowed,this.data.record_id))return false;
    return super.filter(options);
  }
}

export class CollectionEngine {
  constructor(directory, model, splitDocument){this.directory=directory;this.model=model;this.splitDocument=splitDocument;this.collections=null;}
  async load(){
    if(this.collections)return;
    const directory=path.join(this.directory,'engine');
    await mkdir(path.join(directory,'smart_entities'),{recursive:true});
    const fs=new NodeFsSmartFsAdapter({fs_path:directory,is_excluded:()=>false});fs.adapter=fs;
    const config={collections:{smart_entities:{data_adapter:AjsonMultiFileCollectionDataAdapter}},items:{smart_entity:{class:RecordChunk}}};
    const env={config,opts:config,data_fs:fs,settings:{smart_entities:{min_chars:1}},collections:{},
      events:{emit(){}},embedding_models:{default:{instance:this.model}},
      create_env_getter(object){object.env=this;}};
    const collection=new SmartEntities(env,{collection_key:'smart_entities'});
    this.collections=collection;this.fs=fs;
    for(const name of await readdir(path.join(directory,'smart_entities'))){
      if(!/^[a-f0-9-]+-c\d+\.ajson$/.test(name))continue;
      const key=name.slice(0,-6);
      collection.set(new RecordChunk(env,{key}));
      await collection.data_adapter.load_item(key);
      const item=collection.get(key);
      if(item?.data.text)item.init();else delete collection.items[key];
    }
  }
  async inventory(){
    await this.load();
    const documents={};
    for(const item of Object.values(this.collections.items)){
      const id=item.data.record_id;
      if(!id)continue;
      documents[id]??={fingerprint:item.data.fingerprint,signature:item.data.signature,valid:true,chunks:0};
      documents[id].chunks++;
      if(item.is_unembedded||!item.vec?.length)documents[id].valid=false;
    }
    return documents;
  }
  async upsert({record_id,user_id,fingerprint,signature,...document}){
    await this.load();
    const chunks=await this.splitDocument(this.model,document),collection=this.collections;
    const keys=new Set(chunks.map((_,i)=>`${record_id}-c${i}`));
    await this.remove(record_id,keys);
    for(let i=0;i<chunks.length;i++){
      const chunk=chunks[i],key=`${record_id}-c${i}`;
      const hash=createHash('sha256').update(chunk.text+'\n'+signature).digest('hex');
      let item=collection.get(key);
      if(!item){item=new RecordChunk(collection.env,{key});collection.set(item)}
      Object.assign(item.data,{key,path:key,record_id,user_id,fingerprint,signature,text:chunk.text,snippet:chunk.snippet,
        start:chunk.start,end:chunk.end,last_read:{hash}});
      item.deleted=false;
      item._queue_embed=item.is_unembedded;
      item.queue_save();
    }
    // Original queue handles batching, embedding state, and persistence.
    collection._embed_queue=[];
    await collection.process_embed_queue();
    await collection.process_save_queue();
    const result=chunks.map((chunk,i)=>{
      const item=collection.get(`${record_id}-c${i}`);
      if(item.is_unembedded || item._queue_save || !item.vec?.length || item.vec.some(x=>!Number.isFinite(x)))throw Error('COLLECTION_SAVE_OR_EMBED_FAILED');
      return {id:item.key,snippet:chunk.snippet,start:chunk.start,end:chunk.end};
    });
    return result;
  }
  async remove(recordId,keep=new Set()){
    await this.load();
    const removed=Object.values(this.collections.items).filter(item=>item.data.record_id===recordId&&!keep.has(item.key));
    for(const item of removed)item.delete();
    if(removed.length)await this.collections.process_save_queue();
    // Original AJSON appends tombstones; remove obsolete files too so deleted text
    // does not remain in append history. Authoritative source deletion is separate.
    const directory=path.join(this.directory,'engine','smart_entities');
    for(const name of await readdir(directory)){
      if(!name.startsWith(recordId+'-c')||!/^[-a-f0-9]+-c\d+\.ajson$/.test(name)||keep.has(name.slice(0,-6)))continue;
      await unlink(path.join(directory,name)).catch(error=>{if(error.code!=='ENOENT')throw error});
    }
    this.collections._embed_queue=[];
  }
  async search({query,user_id,allowed,signature,limit}){
    await this.load();
    query=(this.model.model?.data?.query_prefix||'')+query;
    if((await this.model.count_tokens(query)).tokens>this.model.max_tokens)throw Error('QUERY_TOO_LONG');
    const queryVector=(await this.model.embed(query)).vec;
    // Ask the original nearest method for enough chunks before grouping into records.
    const matches=await this.collections.entities_vector_adapter.nearest(queryVector,{
      limit:Math.max(1,Object.keys(this.collections.items).length),moss_user:user_id,moss_allowed:allowed});
    if(!Array.isArray(matches))throw Error('COLLECTION_SEARCH_FAILED');
    const seen=new Set(),result=[];
    for(const match of matches){
      const {data}=match.item;
      if(data.signature!==signature||seen.has(data.record_id))continue;
      seen.add(data.record_id);
      result.push({id:match.item.key,record_id:data.record_id,snippet:data.snippet,fingerprint:data.fingerprint,score:match.score});
      if(result.length===limit)break;
    }
    return result;
  }
}
