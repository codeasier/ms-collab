import {validId,displayPolicyId,validateAdminTransferSelection} from './client.mjs'

const interrupted = 'The interrupted administrator change may already have completed. Reconnect or recheck your session before inspecting current state.'
const unknown = 'Result unknown. The server may already have completed this change. Current roles alone do not identify this request. Retry only the original request retained in this page.'
const abandoned = 'The local administrator transfer was cleared, not canceled on the server. Any dispatched change may already have completed. Its retry reference is no longer retained.'

// This lifetime is independent of the administration-list generation. Losing
// administration access clears those lists without destroying an unknown tuple.
export function mountAdminTransfer({client,doc=document,clearAdministration=()=>{},refreshWorkspaces=async()=>{},onUnauthorized=()=>{}}) {
  const el = id => doc.getElementById(id)
  let generation = 0, controller, scope, members = [], available = false
  let state = 'idle', attempt, wasUnknown = false, retainedKey
  const dialog = el('transfer-dialog')
  const target = () => el('transfer-target').value
  let observedTarget = ''
  function setTarget(value) { el('transfer-target').value = value; observedTarget = target() }
  const eligible = value => validId(value) && members.some(member => member.principal_id === value && member.state === 'active')
  function controls() {
    const busy = ['reviewing','submitting'].includes(state)
    el('transfer-open').hidden = !available
    el('transfer-review').disabled = busy || !available || !eligible(target()) || state === 'unknown'
    el('transfer-confirm').disabled = busy || !attempt || !['reviewed','unknown'].includes(state)
    el('transfer-confirm').textContent = state === 'unknown' || (state === 'submitting' && wasUnknown) ? 'Retry original request' : 'Confirm administrator transfer'
    // Changing inputs or closing abandons only this document's ownership, even
    // during a request; neither action promises a server-side cancellation.
    el('transfer-target').disabled = false
    el('transfer-close').disabled = false
    el('transfer-dialog').dataset.state = state
  }
  function resetAttempt(message='') {
    generation++; controller?.abort(); controller = undefined
    state = 'idle'; attempt = undefined; wasUnknown = false; retainedKey = undefined
    el('transfer-selection').textContent = ''; el('transfer-reference').textContent = ''
    el('transfer-status').textContent = message; controls()
  }
  function clear() {
    resetAttempt(); scope = undefined; members = []; available = false
    dialog.close(); el('transfer-target').replaceChildren(); setTarget('')
    el('transfer-outcome').textContent = ''; controls()
  }
  function reference() {
    if (!attempt) return
    el('transfer-reference').textContent = `Local request reference: ${attempt.requestKey}. Workspace: ${attempt.workspace_id}. Actor: ${displayPolicyId(attempt.actor_id)} (reviewed revision ${attempt.actor_revision}). Replacement: ${displayPolicyId(attempt.replacement_id)} (reviewed revision ${attempt.replacement_revision}). This reference is held only in this document, not a recovery credential.`
  }
  async function setMembers(space, values) {
    clear(); scope = space.id; members = values.map(value => ({...value}))
    const placeholder = doc.createElement('option'); placeholder.value = ''; placeholder.textContent = 'Choose a current member'; el('transfer-target').append(placeholder)
    for (const member of members) {
      const option = doc.createElement('option'); option.value = member.principal_id
      option.textContent = `${displayPolicyId(member.principal_id)} · ${member.role} · ${member.state}${!validId(member.principal_id) ? ' (not addressable by this route)' : ''}`
      option.disabled = member.state !== 'active' || !validId(member.principal_id); el('transfer-target').append(option)
    }
    setTarget('')
    if (space.state !== 'active') return
    const turn = generation, workspace = scope
    controller = new AbortController()
    const isCurrent = () => turn === generation && workspace === scope
    try {
      const result = await client.transferCapabilities(workspace,{signal:controller.signal,isCurrent})
      if (!isCurrent()) return
      available = result?.available === true; controls()
    } catch (error) {
      if (!isCurrent() || error.name === 'AbortError') return
      available = false; controls()
      if (error.status === 401) onUnauthorized(interrupted)
    }
  }
  el('transfer-open').addEventListener('click',() => {
    if (!available || !validId(scope)) return
    resetAttempt(); el('transfer-outcome').textContent = ''; setTarget(''); dialog.showModal(); controls()
  })
  for (const event of ['input','change']) el('transfer-target').addEventListener(event,() => {
    // Native selects emit input and then change for the same value. Invalidate
    // once, without replacing a warning that a dispatched attempt was lost.
    if (!dialog.open || target() === observedTarget) return
    observedTarget = target()
    const uncertain = wasUnknown || state === 'submitting'
    resetAttempt(uncertain ? abandoned : 'Explicitly review this member before confirming a transfer.')
  })
  function close() {
    const uncertain = wasUnknown || state === 'submitting'
    resetAttempt(); dialog.close(); setTarget('')
    el('transfer-outcome').textContent = uncertain ? abandoned : ''
  }
  el('transfer-close').addEventListener('click',close)
  dialog.addEventListener('cancel',event => { event.preventDefault(); close() })
  el('transfer-review').addEventListener('click',async () => {
    if (!dialog.open || !available || !eligible(target()) || ['reviewing','submitting','unknown'].includes(state)) return
    const replacement = target(), workspace = scope, reuseKey = retainedKey
    observedTarget = replacement
    resetAttempt(); retainedKey = reuseKey
    const turn = generation; state = 'reviewing'; controller = new AbortController(); controls()
    const isCurrent = () => turn === generation && scope === workspace && target() === replacement && dialog.open
    el('transfer-status').textContent = 'Reviewing current identities and revisions. No administrator change is being submitted.'
    try {
      const reviewed = validateAdminTransferSelection(await client.reviewAdminTransfer(workspace,replacement,{signal:controller.signal,isCurrent}),workspace,replacement)
      if (!isCurrent()) return
      attempt = Object.freeze({...reviewed,requestKey:retainedKey || crypto.randomUUID()}); retainedKey = undefined; state = 'reviewed'
      el('transfer-selection').textContent = `Workspace: ${reviewed.workspace_id}. You: ${displayPolicyId(reviewed.actor_id)}, revision ${reviewed.actor_revision}. Replacement: ${displayPolicyId(reviewed.replacement_id)}, revision ${reviewed.replacement_revision}. The replacement becomes administrator; you become developer and lose management access to this workspace. Confirm only if you intend both changes.`
      el('transfer-status').textContent = 'Review complete. Confirm explicitly to submit this exact selection.'; reference()
    } catch (error) {
      if (!isCurrent() || error.name === 'AbortError') return
      state = 'idle'
      if (error.status === 401) { onUnauthorized(interrupted); return }
      el('transfer-status').textContent = 'This selection could not be reviewed. No transfer was submitted. Review an eligible current member again.'
    } finally { if (isCurrent()) controls() }
  })
  el('transfer-form').addEventListener('submit',async event => {
    event.preventDefault()
    if (!dialog.open || !attempt || !['reviewed','unknown'].includes(state) || !validId(scope) || !validId(target()) || (!wasUnknown && !eligible(target())) || target() !== attempt.replacement_id || scope !== attempt.workspace_id) return
    const original = attempt, turn = generation
    state = 'submitting'; controller = new AbortController(); controls()
    const isCurrent = () => generation === turn && attempt === original && scope === original.workspace_id && target() === original.replacement_id && dialog.open
    el('transfer-status').textContent = 'Submitting the reviewed request. Leaving or editing clears only this page; server work may still complete.'
    try {
      await client.submitAdminTransfer(original,{signal:controller.signal,isCurrent})
      if (!isCurrent()) return
      state = 'committed'; available = false
      // Fix the known outcome before any optional refresh can fail. Do not use
      // the generic reload chain: the actor has intentionally lost admin access.
      clearAdministration(original.workspace_id)
      dialog.close(); el('transfer-selection').textContent = ''; el('transfer-reference').textContent = ''; el('transfer-status').textContent = ''; el('transfer-target').replaceChildren(); setTarget('')
      attempt = undefined; members = []; wasUnknown = false
      el('transfer-outcome').textContent = 'Administrator transfer committed. The reviewed request promoted the replacement and changed your role to developer. A replay reports that historical operation, not current roles.'
      controls()
      try { await refreshWorkspaces(original.workspace_id) } catch { /* The verified commit remains committed. */ }
    } catch (error) {
      if (!isCurrent()) return
      if (error.status === 401) { onUnauthorized(interrupted); return }
      if (!wasUnknown && error.status === 412 && error.adminTransferRejected === true) {
        retainedKey = original.requestKey; attempt = undefined; state = 'definite-no-write'
        el('transfer-selection').textContent = ''; el('transfer-reference').textContent = ''
        el('transfer-status').textContent = 'This invocation was rejected before writing. Explicitly review again, then confirm. The unused retry key is retained.'
      } else {
        wasUnknown = true; state = 'unknown'
        if (error.status === 403 || error.status === 404) {
          available = false; clearAdministration(original.workspace_id)
          // Keep only the original retry tuple, not the now-denied member list.
          members = []; el('transfer-target').replaceChildren()
          const retained = doc.createElement('option'); retained.value = original.replacement_id; retained.textContent = displayPolicyId(original.replacement_id)
          el('transfer-target').append(retained); setTarget(original.replacement_id)
        }
        el('transfer-status').textContent = unknown; reference()
      }
    } finally { if (generation === turn) controls() }
  })
  controls()
  return {setMembers,clear}
}
