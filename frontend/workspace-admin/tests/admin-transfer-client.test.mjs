import * as codec from '../client.mjs'
import {adminTransferClientCases} from './admin-transfer-client-cases.mjs'
adminTransferClientCases('Bearer',async fetcher=>{const client=codec.createClient({fetcher});client.connect('synthetic-only');return client},codec)
