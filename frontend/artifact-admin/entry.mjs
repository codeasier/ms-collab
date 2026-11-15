// Explicit standalone bearer entry, never served by the cookie-only product host.
import {createClient} from './client.mjs'
import {mountArtifactAdministrationPage} from './browser.mjs'
mountArtifactAdministrationPage({client:createClient()})
