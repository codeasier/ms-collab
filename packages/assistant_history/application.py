# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Authenticated saved history and reversible metadata, without an agent runtime."""
from urllib.parse import parse_qsl
import re

from packages.workspace_api import WorkspaceApplication
from packages.workspace_api.application import _object
from packages.workspaces import PolicyError, StorageError
from .model import DisplayEvent, MAX_REVISION, decode
from .store import authorized_row, audit, public, validate_schema

_SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,199}\Z")


class AssistantHistoryApplication(WorkspaceApplication):
    def _dispatch(self, environ):
        assertion=self._verify_request(environ)
        workspace, conversation, action, options=self._route(environ)
        state=expected=None
        if action == "lifecycle":
            expected=self._revision(environ)
            state=self._body(environ,{"state"})["state"]
            if type(state) is not str or state not in {"active","archived"}:raise PolicyError(400,"invalid_state")
        def operation(policy, owner, connection):
            validate_schema(connection)
            policy.authorize(owner,workspace,"assistant","write" if action == "lifecycle" else "read")
            if action == "list":
                ids=connection.execute("SELECT id FROM assistant_conversations WHERE workspace_id=? AND owner_id=? AND state=? ORDER BY created_at DESC,id LIMIT 101",(workspace,owner,options["state"])).fetchall()
                if len(ids)>100:raise StorageError("invalid_assistant_record")
                rows=[]
                for (identity,) in ids:
                    try:row=authorized_row(policy,connection,owner,workspace,identity)
                    except PolicyError:continue  # Current source dependency/fence failed; no label leaks.
                    rows.append(self._public(policy,owner,row))
                return {"workspace_id":workspace,"state":options["state"],"items":rows,
                        "execution_enabled":False,"history_policy":"stored_display_only;no_replay"}
            row=authorized_row(policy,connection,owner,workspace,conversation,"write" if action=="lifecycle" else "read")
            if action == "lifecycle":
                policy._match(expected,row["revision"])
                if row["state"] != state:
                    if row["revision"] == MAX_REVISION:raise PolicyError(409,"conversation_limit")
                    changed=connection.execute("UPDATE assistant_conversations SET state=?,revision=revision+1 WHERE workspace_id=? AND id=? AND owner_id=? AND revision=?",(state,workspace,conversation,owner,row["revision"])).rowcount
                    if changed != 1:raise PolicyError(412,"stale_revision")
                    row.update(state=state,revision=row["revision"]+1)
                    audit(policy,owner,workspace,conversation,row["revision"])
                return self._public(policy,owner,row)
            if action == "metadata":return self._public(policy,owner,row)
            after,limit,revision=options["after"],options["limit"],options.get("revision")
            if after and revision is None:raise PolicyError(428,"precondition_required")
            if revision is not None:policy._match(revision,row["revision"])
            if after>row["event_count"]:raise PolicyError(400,"invalid_position")
            rows=connection.execute("SELECT sequence,event FROM assistant_events WHERE workspace_id=? AND conversation_id=? AND sequence>? ORDER BY sequence LIMIT ?",(workspace,conversation,after,limit)).fetchall()
            try:
                events=[dict(sequence=sequence,**DisplayEvent.from_dict(decode(value)).as_dict()) for sequence,value in rows]
                if [item["sequence"] for item in events] != list(range(after+1,min(after+limit,row["event_count"])+1)):
                    raise ValueError()
            except (ValueError,TypeError,KeyError,UnicodeError,RecursionError,OverflowError):
                raise StorageError("invalid_assistant_record") from None
            end=after+len(events)
            return {"workspace_id":workspace,"conversation_id":conversation,"revision":row["revision"],
                    "items":events,"total":row["event_count"],"after":after,"limit":limit,
                    "next_after":end if end<row["event_count"] else None,"has_more":end<row["event_count"],
                    "history_policy":"stored_display_only;no_replay","execution_enabled":False}
        return self._authenticated_transaction(assertion,operation,write=action=="lifecycle",with_connection=True)

    @staticmethod
    def _public(policy,owner,row):
        writable=True
        try:policy.authorize(owner,row["workspace_id"],"assistant","write")
        except PolicyError:writable=False
        return dict(public(row), capabilities={"events":True,"archive":writable and row["state"]=="active",
            "restore":writable and row["state"]=="archived","execute":False,"resume":False,"export":False})

    @staticmethod
    def _route(environ):
        path,verb,query=environ.get("PATH_INFO",""),environ.get("REQUEST_METHOD",""),environ.get("QUERY_STRING","")
        if type(path) is not str or len(path)>1000 or type(query) is not str or len(query)>256:
            raise PolicyError(400,"invalid_request")
        parts=path.split("/")
        if (len(parts) not in {6,7,8} or parts[:3]!=["","api","workspaces"] or parts[4:6]!=["assistant","conversations"]
                or any(not _SEGMENT.fullmatch(p) for p in parts[3:])):
            raise PolicyError(404,"not_found")
        workspace,conversation=parts[3],parts[6] if len(parts)>6 else None
        if len(parts)==7 and not query and verb in {"GET","PATCH"}:
            return workspace,conversation,"metadata" if verb=="GET" else "lifecycle",{}
        if verb != "GET" or (len(parts)==8 and parts[7]!="events") or len(parts)==7:
            raise PolicyError(404,"not_found")
        try:
            options=_object(parse_qsl(query,keep_blank_values=True,strict_parsing=True,max_num_fields=3))
            if len(parts)==6:
                if set(options)-{"state"} or options.get("state","active") not in {"active","archived"}:raise ValueError()
                return workspace,None,"list",{"state":options.get("state","active")}
            if set(options)-{"after","limit","revision"}:raise ValueError()
            if any(not re.fullmatch(r"[0-9]{1,16}",value) for value in options.values()):raise ValueError()
            options={key:int(value) for key,value in options.items()}
            if not 0<=options.get("after",0)<=2000 or not 1<=options.get("limit",50)<=100 or ("revision" in options and not 1<=options["revision"]<=MAX_REVISION):raise ValueError()
            return workspace,conversation,"events",{"after":0,"limit":50,**options}
        except (ValueError,TypeError):raise PolicyError(400,"invalid_request") from None
