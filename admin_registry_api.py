"""Admin facility registry + emergency command workflow API routes."""
from flask import jsonify, request, session
from werkzeug.security import generate_password_hash

import facility_registry as fr
import hospital_logic as hl


def register_admin_registry_routes(app, deps):
    """
    deps keys:
      admin_required, _require_admin_perm, read_json, save_json,
      load_users, load_emergencies, save_emergencies, save_users,
      append_audit, user_name, _append_status, TEAM_LABELS,
      ACTIVE_SOS_STATUSES, STATUS_VALUES, COMPLETED_STATUSES,
      normalize_emergency_record, now_str, _notify_targets (optional callable),
      normalize_email, signup_email_rejection_reason, allow_test_email_domains,
      _password_policy_error, _link_user_to_hospital
    """
    admin_required = deps["admin_required"]
    _require_admin_perm = deps["_require_admin_perm"]
    read_json = deps["read_json"]
    save_json = deps["save_json"]
    load_users = deps["load_users"]
    load_emergencies = deps["load_emergencies"]
    save_emergencies = deps["save_emergencies"]
    save_users = deps["save_users"]
    append_audit = deps["append_audit"]
    user_name = deps["user_name"]
    _append_status = deps["_append_status"]
    TEAM_LABELS = deps["TEAM_LABELS"]
    ACTIVE_SOS_STATUSES = deps["ACTIVE_SOS_STATUSES"]
    STATUS_VALUES = deps["STATUS_VALUES"]
    COMPLETED_STATUSES = deps["COMPLETED_STATUSES"]
    normalize_emergency_record = deps["normalize_emergency_record"]
    now_str = deps["now_str"]
    notify = deps.get("notify") or (lambda *a, **k: None)
    normalize_email = deps["normalize_email"]
    signup_email_rejection_reason = deps["signup_email_rejection_reason"]
    allow_test_email_domains = deps["allow_test_email_domains"]
    _password_policy_error = deps["_password_policy_error"]
    _link_user_to_hospital = deps["_link_user_to_hospital"]

    def _audit_history(entity_type, entity_id, limit=40):
        log = read_json("audit_log", {"entries": [], "next_id": 1})
        eid = int(entity_id)
        rows = [
            e for e in (log.get("entries") or [])
            if e.get("entity_type") == entity_type and e.get("entity_id") == eid
        ]
        return rows[:limit]

    def _linked_users(field, fid):
        udata = load_users()
        out = []
        for u in udata.get("users") or []:
            if u.get(field) == fid:
                out.append({
                    "id": u["id"],
                    "name": user_name(u),
                    "email": u.get("email"),
                    "role": u.get("role"),
                    "status": u.get("status"),
                })
        return out

    # ---------- Hospitals ----------
    @app.route("/api/admin/hospitals", methods=["GET", "POST"])
    @admin_required
    def admin_hospitals_collection():
        denied = _require_admin_perm("users_ops")
        if denied:
            return denied
        if request.method == "GET":
            hdata = hl.load_hospitals(read_json, save_json)
            udata = load_users()
            owners = {
                u["id"]: {"id": u["id"], "name": user_name(u), "email": u.get("email", "")}
                for u in udata.get("users") or []
            }
            q = (request.args.get("q") or "").strip().lower()
            status_f = (request.args.get("status") or "").strip().lower()
            rows = []
            for h in hdata.get("hospitals") or []:
                owner = owners.get(h.get("owner_user_id"))
                if status_f and (h.get("operating_status") or "").lower() != status_f:
                    continue
                if q:
                    blob = " ".join(
                        [
                            str(h.get(k) or "")
                            for k in ("name", "city", "district", "region", "address", "phone", "contact_email")
                        ]
                        + [(owner or {}).get("name") or "", (owner or {}).get("email") or ""]
                    ).lower()
                    if q not in blob:
                        continue
                rows.append({
                    **{k: h.get(k) for k in (
                        "id", "name", "city", "region", "district", "address", "phone",
                        "contact_email", "operating_status", "ambulance_available",
                        "ambulance_count", "emergency_capacity", "latitude", "longitude",
                        "owner_user_id", "services", "specialties", "updated_at", "created_at",
                    )},
                    "owner_name": (owner or {}).get("name") or "",
                    "owner_email": (owner or {}).get("email") or "",
                    "source": "mysql_hospitals",
                })
            rows.sort(key=lambda r: int(r.get("id") or 0))
            return jsonify({
                "success": True, "hospitals": rows, "count": len(rows),
                "source": "mysql", "table": "hospitals",
            })
        # POST create — classic flow: hospital details + login (name/email/password)
        data = request.get_json(silent=True) or {}
        owner_name = (data.get("owner_name") or data.get("account_name") or "").strip()
        owner_email = normalize_email(
            data.get("owner_email") or data.get("login_email") or data.get("email") or ""
        )
        owner_password = (
            data.get("owner_password") or data.get("login_password") or data.get("password") or ""
        ).strip()
        if not owner_name or len(owner_name) < 2:
            return jsonify({
                "success": False,
                "message": "Login full name is required (so the hospital can sign in).",
            }), 400
        if not owner_email:
            return jsonify({
                "success": False,
                "message": "Login email is required (so the hospital can sign in).",
            }), 400
        reject = signup_email_rejection_reason(owner_email)
        if reject and not allow_test_email_domains():
            return jsonify({"success": False, "message": reject}), 400
        udata = load_users()
        if any((u.get("email") or "").lower() == owner_email for u in udata.get("users") or []):
            return jsonify({"success": False, "message": "Email already registered"}), 400
        pw_err = _password_policy_error(owner_password)
        if pw_err:
            return jsonify({"success": False, "message": pw_err}), 400
        if not (data.get("contact_email") or "").strip():
            data["contact_email"] = owner_email
        try:
            hospital = hl.create_hospital(data, read_json, save_json)
        except ValueError as e:
            return jsonify({"success": False, "message": str(e)}), 400
        uid = udata["next_id"]
        udata["next_id"] = int(uid) + 1
        user = {
            "id": uid,
            "name": owner_name,
            "email": owner_email,
            "phone": (data.get("phone") or "").strip(),
            "password_hash": generate_password_hash(owner_password),
            "role": "hospital",
            "status": "active",
            "email_verified": True,
            "email_verify_token": None,
            "email_verify_expires": None,
            "hospital_id": hospital["id"],
            "station_id": None,
            "call_center_id": None,
            "created_at": now_str(),
            "last_login": None,
            "created_by": session.get("user_id"),
            "activity": [{
                "action": "Hospital account created with facility",
                "timestamp": now_str(),
            }],
        }
        udata["users"].append(user)
        try:
            save_users(udata)
            _link_user_to_hospital(uid, hospital["id"], set_owner=True)
        except Exception:
            # Roll back facility if login account could not be saved
            try:
                hl.delete_hospital(
                    hospital["id"], read_json, save_json,
                    load_emergencies(), load_users(),
                )
            except Exception:
                pass
            raise
        append_audit(
            "hospital_created",
            "hospital",
            hospital["id"],
            {
                "name": hospital.get("name"),
                "owner_user_id": uid,
                "owner_email": owner_email,
            },
            session.get("user_id"),
        )
        append_audit(
            "admin_user_created",
            "user",
            uid,
            {"role": "hospital", "email": owner_email, "name": owner_name, "hospital_id": hospital["id"]},
            session.get("user_id"),
        )
        hdata = hl.load_hospitals(read_json, save_json)
        hospital = hl.get_hospital_by_id(hdata, hospital["id"]) or hospital
        return jsonify({
            "success": True,
            "hospital": hospital,
            "owner": {"id": uid, "name": owner_name, "email": owner_email, "role": "hospital"},
        }), 201

    @app.route("/api/admin/hospitals/<int:hid>", methods=["GET", "PUT", "DELETE"])
    @admin_required
    def admin_hospital_item(hid):
        denied = _require_admin_perm("users_ops")
        if denied:
            return denied
        hdata = hl.load_hospitals(read_json, save_json)
        hospital = hl.get_hospital_by_id(hdata, hid)
        if not hospital:
            return jsonify({"success": False, "message": "Hospital not found"}), 404
        if request.method == "GET":
            return jsonify({
                "success": True,
                "hospital": hospital,
                "linked_users": _linked_users("hospital_id", hid),
                "history": _audit_history("hospital", hid),
            })
        if request.method == "PUT":
            data = request.get_json(silent=True) or {}
            try:
                hospital = hl.update_hospital(hid, data, read_json, save_json)
            except ValueError as e:
                return jsonify({"success": False, "message": str(e)}), 400
            append_audit("hospital_updated", "hospital", hid, {"fields": list(data.keys())}, session.get("user_id"))
            return jsonify({"success": True, "hospital": hospital})
        # DELETE
        edata = load_emergencies()
        udata = load_users()
        try:
            users_changed = hl.delete_hospital(hid, read_json, save_json, edata, udata)
        except ValueError as e:
            return jsonify({"success": False, "message": str(e)}), 400
        if users_changed:
            save_users(udata)
        append_audit("hospital_deleted", "hospital", hid, {}, session.get("user_id"))
        return jsonify({"success": True})

    @app.route("/api/admin/hospitals/<int:hid>/toggle", methods=["POST"])
    @admin_required
    def admin_hospital_toggle(hid):
        denied = _require_admin_perm("users_ops")
        if denied:
            return denied
        hdata = hl.load_hospitals(read_json, save_json)
        hospital = hl.get_hospital_by_id(hdata, hid)
        if not hospital:
            return jsonify({"success": False, "message": "Hospital not found"}), 404
        cur = (hospital.get("operating_status") or "open").lower()
        nxt = "closed" if cur != "closed" else "open"
        try:
            hospital = hl.update_hospital(hid, {"operating_status": nxt}, read_json, save_json)
        except ValueError as e:
            return jsonify({"success": False, "message": str(e)}), 400
        append_audit("hospital_toggle", "hospital", hid, {"operating_status": nxt}, session.get("user_id"))
        return jsonify({"success": True, "hospital": hospital})

    # ---------- Stations (police / fire) ----------
    @app.route("/api/admin/stations", methods=["GET", "POST"])
    @admin_required
    def admin_stations_collection():
        denied = _require_admin_perm("users_ops")
        if denied:
            return denied
        if request.method == "GET":
            kind = (request.args.get("kind") or "").strip().lower()
            q = (request.args.get("q") or "").strip().lower()
            status_f = (request.args.get("status") or "").strip().lower()
            data = fr.load_stations(read_json)
            rows = []
            for s in data.get("stations") or []:
                if kind and s.get("kind") != kind:
                    continue
                if status_f and (s.get("operating_status") or "").lower() != status_f:
                    continue
                if q:
                    blob = " ".join(str(s.get(k) or "") for k in ("name", "city", "district", "region", "phone", "address")).lower()
                    if q not in blob:
                        continue
                rows.append(s)
            rows.sort(key=lambda r: int(r.get("id") or 0))
            return jsonify({"success": True, "stations": rows, "count": len(rows)})
        data = request.get_json(silent=True) or {}
        try:
            row = fr.create_station(data, read_json, save_json)
        except ValueError as e:
            return jsonify({"success": False, "message": str(e)}), 400
        append_audit("station_created", "station", row["id"], {"kind": row.get("kind"), "name": row.get("name")}, session.get("user_id"))
        return jsonify({"success": True, "station": row}), 201

    @app.route("/api/admin/stations/<int:sid>", methods=["GET", "PUT", "DELETE"])
    @admin_required
    def admin_station_item(sid):
        denied = _require_admin_perm("users_ops")
        if denied:
            return denied
        data = fr.load_stations(read_json)
        row = fr.get_station(data, sid)
        if not row:
            return jsonify({"success": False, "message": "Station not found"}), 404
        if request.method == "GET":
            return jsonify({
                "success": True,
                "station": row,
                "linked_users": _linked_users("station_id", sid),
                "history": _audit_history("station", sid),
            })
        if request.method == "PUT":
            payload = request.get_json(silent=True) or {}
            try:
                row = fr.update_station(sid, payload, read_json, save_json)
            except ValueError as e:
                return jsonify({"success": False, "message": str(e)}), 400
            append_audit("station_updated", "station", sid, {"fields": list(payload.keys())}, session.get("user_id"))
            return jsonify({"success": True, "station": row})
        udata = load_users()
        edata = load_emergencies()
        try:
            _, udata, users_changed = fr.delete_station(sid, read_json, save_json, udata, edata)
        except ValueError as e:
            return jsonify({"success": False, "message": str(e)}), 400
        if users_changed:
            save_users(udata)
        append_audit("station_deleted", "station", sid, {}, session.get("user_id"))
        return jsonify({"success": True})

    @app.route("/api/admin/stations/<int:sid>/toggle", methods=["POST"])
    @admin_required
    def admin_station_toggle(sid):
        denied = _require_admin_perm("users_ops")
        if denied:
            return denied
        data = fr.load_stations(read_json)
        row = fr.get_station(data, sid)
        if not row:
            return jsonify({"success": False, "message": "Station not found"}), 404
        nxt = "closed" if (row.get("operating_status") or "open") != "closed" else "open"
        try:
            row = fr.update_station(sid, {"operating_status": nxt}, read_json, save_json)
        except ValueError as e:
            return jsonify({"success": False, "message": str(e)}), 400
        append_audit("station_toggle", "station", sid, {"operating_status": nxt}, session.get("user_id"))
        return jsonify({"success": True, "station": row})

    # ---------- Ambulances (admin = read-only dispatch board; hospitals own units) ----------
    _AMB_HOSPITAL_OWNED = (
        "Hospitals manage their own ambulances. "
        "GurmadNet only shows dispatch status (availability, GPS, driver contact)."
    )

    @app.route("/api/admin/ambulances", methods=["GET", "POST"])
    @admin_required
    def admin_ambulances_collection():
        denied = _require_admin_perm("users_ops")
        if denied:
            return denied
        if request.method == "POST":
            return jsonify({"success": False, "message": _AMB_HOSPITAL_OWNED}), 403
        hdata = hl.load_hospitals(read_json, save_json)
        hnames = {h["id"]: h.get("name") for h in hdata.get("hospitals") or []}
        q = (request.args.get("q") or "").strip().lower()
        status_f = (request.args.get("status") or "").strip().lower()
        if status_f == "maintenance":
            status_f = "offline"
        hid = request.args.get("hospital_id", type=int)
        data = fr.load_ambulances(read_json)
        rows = []
        for a in data.get("ambulances") or []:
            if hid and a.get("hospital_id") != hid:
                continue
            view = fr.ambulance_dispatch_view(a, hnames.get(a.get("hospital_id"), ""))
            if status_f and (view.get("status") or "").lower() != status_f:
                continue
            if q:
                blob = " ".join([
                    str(view.get("call_sign") or ""),
                    str(view.get("driver_name") or ""),
                    str(view.get("driver_phone") or ""),
                    str(view.get("hospital_name") or ""),
                ]).lower()
                if q not in blob:
                    continue
            rows.append(view)
        rows.sort(key=lambda r: int(r.get("id") or 0))
        return jsonify({
            "success": True,
            "ambulances": rows,
            "count": len(rows),
            "managed_by": "hospital",
            "note": _AMB_HOSPITAL_OWNED,
        })

    @app.route("/api/admin/ambulances/<int:aid>", methods=["GET", "PUT", "DELETE"])
    @admin_required
    def admin_ambulance_item(aid):
        denied = _require_admin_perm("users_ops")
        if denied:
            return denied
        if request.method in ("PUT", "DELETE"):
            return jsonify({"success": False, "message": _AMB_HOSPITAL_OWNED}), 403
        data = fr.load_ambulances(read_json)
        row = fr.get_ambulance(data, aid)
        if not row:
            return jsonify({"success": False, "message": "Ambulance not found"}), 404
        hdata = hl.load_hospitals(read_json, save_json)
        h = hl.get_hospital_by_id(hdata, row.get("hospital_id"))
        return jsonify({
            "success": True,
            "ambulance": fr.ambulance_dispatch_view(row, (h or {}).get("name") or ""),
            "history": _audit_history("ambulance", aid),
            "managed_by": "hospital",
        })

    @app.route("/api/admin/ambulances/<int:aid>/toggle", methods=["POST"])
    @admin_required
    def admin_ambulance_toggle(aid):
        denied = _require_admin_perm("users_ops")
        if denied:
            return denied
        return jsonify({"success": False, "message": _AMB_HOSPITAL_OWNED}), 403

    # ---------- Call centers ----------
    @app.route("/api/admin/call-centers", methods=["GET", "POST"])
    @admin_required
    def admin_call_centers_collection():
        denied = _require_admin_perm("users_ops")
        if denied:
            return denied
        if request.method == "GET":
            q = (request.args.get("q") or "").strip().lower()
            status_f = (request.args.get("status") or "").strip().lower()
            data = fr.load_call_centers(read_json)
            rows = []
            for c in data.get("call_centers") or []:
                if status_f and (c.get("operating_status") or "").lower() != status_f:
                    continue
                if q:
                    blob = " ".join(str(c.get(k) or "") for k in ("name", "city", "district", "phone", "address")).lower()
                    if q not in blob:
                        continue
                rows.append(c)
            rows.sort(key=lambda r: int(r.get("id") or 0))
            return jsonify({"success": True, "call_centers": rows, "count": len(rows)})
        payload = request.get_json(silent=True) or {}
        try:
            row = fr.create_call_center(payload, read_json, save_json)
        except ValueError as e:
            return jsonify({"success": False, "message": str(e)}), 400
        append_audit("call_center_created", "call_center", row["id"], {"name": row.get("name")}, session.get("user_id"))
        return jsonify({"success": True, "call_center": row}), 201

    @app.route("/api/admin/call-centers/<int:cid>", methods=["GET", "PUT", "DELETE"])
    @admin_required
    def admin_call_center_item(cid):
        denied = _require_admin_perm("users_ops")
        if denied:
            return denied
        data = fr.load_call_centers(read_json)
        row = fr.get_call_center(data, cid)
        if not row:
            return jsonify({"success": False, "message": "Call center not found"}), 404
        if request.method == "GET":
            return jsonify({
                "success": True,
                "call_center": row,
                "linked_users": _linked_users("call_center_id", cid),
                "history": _audit_history("call_center", cid),
            })
        if request.method == "PUT":
            payload = request.get_json(silent=True) or {}
            try:
                row = fr.update_call_center(cid, payload, read_json, save_json)
            except ValueError as e:
                return jsonify({"success": False, "message": str(e)}), 400
            append_audit("call_center_updated", "call_center", cid, {"fields": list(payload.keys())}, session.get("user_id"))
            return jsonify({"success": True, "call_center": row})
        udata = load_users()
        try:
            users_changed = fr.delete_call_center(cid, read_json, save_json, udata)
        except ValueError as e:
            return jsonify({"success": False, "message": str(e)}), 400
        if users_changed:
            save_users(udata)
        append_audit("call_center_deleted", "call_center", cid, {}, session.get("user_id"))
        return jsonify({"success": True})

    @app.route("/api/admin/call-centers/<int:cid>/toggle", methods=["POST"])
    @admin_required
    def admin_call_center_toggle(cid):
        denied = _require_admin_perm("users_ops")
        if denied:
            return denied
        data = fr.load_call_centers(read_json)
        row = fr.get_call_center(data, cid)
        if not row:
            return jsonify({"success": False, "message": "Call center not found"}), 404
        nxt = "closed" if (row.get("operating_status") or "open") != "closed" else "open"
        try:
            row = fr.update_call_center(cid, {"operating_status": nxt}, read_json, save_json)
        except ValueError as e:
            return jsonify({"success": False, "message": str(e)}), 400
        append_audit("call_center_toggle", "call_center", cid, {"operating_status": nxt}, session.get("user_id"))
        return jsonify({"success": True, "call_center": row})

    # ---------- Command workflows ----------
    def _find_emergency(edata, eid):
        for em in edata.get("emergencies") or []:
            if em.get("id") == eid:
                return em
        return None

    def _apply_facility_assign(em, data):
        assigned_to = (data.get("assigned_to") or em.get("assigned_to") or "hospital").lower()
        if assigned_to not in ("hospital", "police", "fire"):
            raise ValueError("assigned_to must be hospital, police, or fire")
        em["assigned_to"] = assigned_to
        em["assigned_team_label"] = data.get("assigned_team_label") or TEAM_LABELS.get(assigned_to, assigned_to.title())
        if assigned_to == "hospital":
            hid = data.get("assigned_hospital_id")
            if hid is not None and hid != "":
                hid = int(hid)
                h = hl.get_hospital_by_id(hl.load_hospitals(read_json, save_json), hid)
                if not h:
                    raise ValueError("Hospital not found")
                em["assigned_hospital_id"] = hid
                em["assigned_hospital_name"] = h.get("name") or ""
                em["assigned_station_id"] = None
            amb = data.get("ambulance_unit_id")
            if amb:
                aid = int(amb)
                adata = fr.load_ambulances(read_json)
                unit = fr.get_ambulance(adata, aid)
                if not unit:
                    raise ValueError("Ambulance not found")
                if em.get("assigned_hospital_id") and unit.get("hospital_id") != em.get("assigned_hospital_id"):
                    raise ValueError("Ambulance does not belong to the assigned hospital")
                if (unit.get("status") or "").lower() not in ("available", "busy"):
                    # allow re-assign busy; reject offline
                    if (unit.get("status") or "").lower() == "offline":
                        raise ValueError("Ambulance is offline")
                em["assigned_ambulance_id"] = aid
                em["assigned_ambulance_call_sign"] = unit.get("call_sign") or ""
                em["assigned_ambulance_driver_name"] = unit.get("driver_name") or ""
                em["assigned_ambulance_driver_phone"] = unit.get("driver_phone") or ""
                em["assigned_ambulance_latitude"] = unit.get("latitude")
                em["assigned_ambulance_longitude"] = unit.get("longitude")
                if (unit.get("status") or "").lower() == "available":
                    fr.mark_ambulance_busy(aid, read_json, save_json)
                    hdata2 = hl.load_hospitals(read_json, save_json)
                    adata2 = fr.load_ambulances(read_json)
                    if fr.sync_hospital_ambulance_counts(hdata2, adata2):
                        hl.save_hospitals(hdata2, save_json)
        else:
            sid = data.get("assigned_station_id")
            if sid is not None and sid != "":
                sid = int(sid)
                st = fr.get_station(fr.load_stations(read_json), sid)
                if not st:
                    raise ValueError("Station not found")
                if st.get("kind") != assigned_to:
                    raise ValueError("Station kind does not match assigned_to")
                em["assigned_station_id"] = sid
                em["assigned_team_label"] = st.get("name") or em["assigned_team_label"]
                em["assigned_hospital_id"] = None
                em["assigned_hospital_name"] = ""

    @app.route("/api/admin/emergencies/dispatch", methods=["POST"])
    @admin_required
    def admin_emergencies_dispatch():
        denied = _require_admin_perm("emergencies_update")
        if denied:
            return denied
        data = request.get_json(silent=True) or {}
        eid = int(data.get("id") or 0)
        edata = load_emergencies()
        em = _find_emergency(edata, eid)
        if not em:
            return jsonify({"success": False, "message": "Emergency not found"}), 404
        normalize_emergency_record(em)
        try:
            _apply_facility_assign(em, data)
        except (ValueError, TypeError) as e:
            return jsonify({"success": False, "message": str(e)}), 400
        note = (data.get("notes") or "Admin dispatch").strip()
        if em.get("assigned_to") == "hospital" and em.get("assigned_hospital_id"):
            _append_status(em, "pending_hospital", note)
            settings = deps.get("load_settings", lambda: {})()
            timeout = int(settings.get("hospital_response_timeout_sec", 120) if settings else 120)
            from datetime import datetime, timedelta
            em["response_deadline"] = (datetime.utcnow() + timedelta(seconds=timeout)).strftime("%Y-%m-%d %H:%M:%S")
            if not em.get("escalation_queue"):
                hdata = hl.load_hospitals(read_json, save_json)
                try:
                    lat, lng = float(em.get("latitude")), float(em.get("longitude"))
                    em["escalation_queue"] = hl.build_escalation_queue(lat, lng, hdata)
                    em["escalation_index"] = 0
                except (TypeError, ValueError):
                    pass
            notify("hospital", em.get("assigned_hospital_id"), f"Dispatch: emergency #{eid}", eid, "dispatch")
        else:
            _append_status(em, "dispatched", note)
            if em.get("assigned_station_id"):
                notify(em.get("assigned_to"), em.get("assigned_station_id"), f"Dispatch: emergency #{eid}", eid, "dispatch")
        save_emergencies(edata)
        append_audit("emergency_dispatch", "emergency", eid, {
            "assigned_to": em.get("assigned_to"),
            "assigned_hospital_id": em.get("assigned_hospital_id"),
            "assigned_station_id": em.get("assigned_station_id"),
        }, session.get("user_id"))
        return jsonify({"success": True, "emergency": em})

    @app.route("/api/admin/emergencies/escalate", methods=["POST"])
    @admin_required
    def admin_emergencies_escalate():
        denied = _require_admin_perm("emergencies_update")
        if denied:
            return denied
        data = request.get_json(silent=True) or {}
        eid = int(data.get("id") or 0)
        edata = load_emergencies()
        em = _find_emergency(edata, eid)
        if not em:
            return jsonify({"success": False, "message": "Emergency not found"}), 404
        normalize_emergency_record(em)
        hdata = hl.load_hospitals(read_json, save_json)
        settings = deps.get("load_settings", lambda: {})()
        timeout = int(settings.get("hospital_response_timeout_sec", 120) if settings else 120)
        if not em.get("escalation_queue"):
            try:
                lat, lng = float(em.get("latitude")), float(em.get("longitude"))
                em["escalation_queue"] = hl.build_escalation_queue(lat, lng, hdata)
                em["escalation_index"] = 0
            except (TypeError, ValueError):
                return jsonify({"success": False, "message": "Cannot build escalation queue without GPS"}), 400
        # Advance past current assignment
        em["escalation_index"] = int(em.get("escalation_index") or 0) + 1
        hospital = hl.assign_next_hospital(em, hdata, timeout)
        if not hospital:
            _append_status(em, "no_hospital_available", "Escalation exhausted")
            save_emergencies(edata)
            append_audit("emergency_escalate", "emergency", eid, {"result": "exhausted"}, session.get("user_id"))
            return jsonify({"success": True, "emergency": em, "message": "No more hospitals in queue"})
        _append_status(em, "pending_hospital", f"Escalated to {hospital.get('name')}")
        notify("hospital", hospital.get("id"), f"Escalated emergency #{eid}", eid, "escalation")
        save_emergencies(edata)
        append_audit("emergency_escalate", "emergency", eid, {"hospital_id": hospital.get("id")}, session.get("user_id"))
        return jsonify({"success": True, "emergency": em})

    @app.route("/api/admin/emergencies/approve", methods=["POST"])
    @admin_required
    def admin_emergencies_approve():
        denied = _require_admin_perm("emergencies_update")
        if denied:
            return denied
        data = request.get_json(silent=True) or {}
        eid = int(data.get("id") or 0)
        edata = load_emergencies()
        em = _find_emergency(edata, eid)
        if not em:
            return jsonify({"success": False, "message": "Emergency not found"}), 404
        normalize_emergency_record(em)
        try:
            if data.get("assigned_to") or data.get("assigned_hospital_id") or data.get("assigned_station_id"):
                _apply_facility_assign(em, data)
        except (ValueError, TypeError) as e:
            return jsonify({"success": False, "message": str(e)}), 400
        note = (data.get("notes") or "Human approved assignment").strip()
        em["admin_approved_at"] = now_str()
        em["admin_approved_by"] = session.get("user_id")
        if em.get("assigned_to") == "hospital" and em.get("assigned_hospital_id"):
            _append_status(em, "pending_hospital", note)
        else:
            _append_status(em, "dispatched", note)
        save_emergencies(edata)
        append_audit("emergency_approve", "emergency", eid, {"status": em.get("status")}, session.get("user_id"))
        return jsonify({"success": True, "emergency": em})

    @app.route("/api/admin/emergencies/verify", methods=["POST"])
    @admin_required
    def admin_emergencies_verify():
        denied = _require_admin_perm("emergencies_update")
        if denied:
            return denied
        data = request.get_json(silent=True) or {}
        eid = int(data.get("id") or 0)
        edata = load_emergencies()
        em = _find_emergency(edata, eid)
        if not em:
            return jsonify({"success": False, "message": "Emergency not found"}), 404
        normalize_emergency_record(em)
        note = (data.get("notes") or "Verified by admin").strip()
        em["verified_at"] = now_str()
        em["verified_by"] = session.get("user_id")
        em["verification_note"] = note
        resolve = data.get("resolve") in (True, "true", "1", 1)
        if resolve:
            _append_status(em, "resolved", note)
            em["tracking_active"] = False
        else:
            em.setdefault("status_history", [])
            em["status_history"].append({"status": em.get("status"), "timestamp": now_str(), "note": "verified: " + note})
        save_emergencies(edata)
        append_audit("emergency_verify", "emergency", eid, {"resolve": resolve}, session.get("user_id"))
        return jsonify({"success": True, "emergency": em})
