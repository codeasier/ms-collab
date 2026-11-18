import {createClient} from '../client.mjs'
import {invitationClientCases} from './invitation-client-cases.mjs'
invitationClientCases('Bearer',async(fetcher)=>{const client=createClient({fetcher});client.connect('synthetic-only');return client})
