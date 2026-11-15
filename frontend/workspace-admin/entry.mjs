import {createClient} from './client.mjs'
import {mountWorkspacePage} from './admin.mjs'
mountWorkspacePage({client:createClient()})
