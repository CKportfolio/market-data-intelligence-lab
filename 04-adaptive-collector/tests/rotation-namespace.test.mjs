import test from "node:test";
import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import { mkdtemp, readdir, rm, stat } from "node:fs/promises";
import { RotatingArchiveWriter } from "../lib/rotating-archive-writer.mjs";

async function exists(p){try{await stat(p);return true}catch(e){if(e?.code==='ENOENT')return false;throw e}}

test("adaptive writer uses namespaced live/staging so it can overlap legacy collector", async()=>{
  const root=await mkdtemp(path.join(os.tmpdir(),"writer-ns-"));
  try{
    const w=await new RotatingArchiveWriter(root,{maxBytes:900,liveName:"current-adaptive",collectorId:"test"}).init();
    for(let i=0;i<20;i++) await w.write("x",{tsMs:Date.now()+i,payload:"x".repeat(120)});
    await w.close();
    assert.equal(await exists(path.join(root,"live","current-adaptive","market.jsonl")),true);
    assert.equal(await exists(path.join(root,"live","current","market.jsonl")),false);
    assert.equal(await exists(path.join(root,"staging-adaptive")),true);
    const archives=(await readdir(path.join(root,"archives"))).filter(x=>x.endsWith('.tar.gz'));
    assert.ok(archives.length>0);
  } finally {await rm(root,{recursive:true,force:true})}
});
