import {createClient} from './client.mjs'
import {mountCommunityPage} from './board.mjs'
mountCommunityPage({client:createClient()})
