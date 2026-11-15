// Explicit standalone bearer entry, never served by the cookie-only product host.
import {createClient} from './client.mjs'
import {mountArtifactCatalogPage} from './browser.mjs'
mountArtifactCatalogPage({client:createClient()})
