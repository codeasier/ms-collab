// Valid synthetic fields at an exact UTF-8 JSON wire size, including escaped names.
import assert from 'node:assert/strict'
import {validateFields} from '../client.mjs'
export const wireBytes=value=>new TextEncoder().encode(JSON.stringify(value)).length
export const escapedNames=['😀'.repeat(20),'"'.repeat(20),'\\'.repeat(20),'😀"\\'.repeat(20)]
export function nearLimitFields(bytes=65536,name=escapedNames[3],sourceFields){
 const fields=sourceFields?structuredClone(sourceFields):[{id:'ui',name,kind:'developer',paths:[],owner_ids:['reader'],backup:null}]
 fields[0].name=name;fields[0].paths=[...Array.from({length:63},(_,i)=>String(i).padStart(2,'0')+'/'+('x'.repeat(1021))),'tail']
 const missing=bytes-wireBytes({fields});assert.ok(missing>=0&&missing+4<=1024)
 fields[0].paths[63]+='x'.repeat(missing);validateFields(fields);assert.equal(wireBytes({fields}),bytes)
 return fields
}
