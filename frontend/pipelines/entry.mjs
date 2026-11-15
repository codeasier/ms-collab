import {createClient} from './client.mjs'
import {mountPipelinePage} from './runs.mjs'
mountPipelinePage({client:createClient()})
