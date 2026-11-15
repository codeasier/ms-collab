import {createClient} from './client.mjs'
import {mountResponsibilityPage} from './browser.mjs'
mountResponsibilityPage({client:createClient()})
