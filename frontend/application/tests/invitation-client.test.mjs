import {createSessionClient} from '../client.mjs'
import {invitationClientCases} from '../../workspace-admin/tests/invitation-client-cases.mjs'
invitationClientCases('Cookie',async(fetcher,onInvalid)=>{
 const client=createSessionClient({cookie:()=>'__Host-ms-collab-csrf='+'A'.repeat(43),onInvalid,fetcher:(path,options)=>path==='/auth/session'?Promise.resolve({ok:true,text:async()=>'{"data":{"principal_id":"admin"}}'}):fetcher(path,options)})
 await client.inspect();return client
})
