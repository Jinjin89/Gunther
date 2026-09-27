import type { Overview } from "@gunther/contracts";
import { AtSign, Check, CircleUserRound, Database, Network, Save } from "lucide-react";
import { type FormEvent, useEffect, useMemo, useState } from "react";
import type { UserProfile } from "../shell";

interface AccountPageProps {
  profile: UserProfile;
  overview: Overview;
  onSave: (profile: UserProfile) => void;
}

export function AccountPage({ profile, overview, onSave }: AccountPageProps) {
  const [draft, setDraft] = useState(profile);
  const [saved, setSaved] = useState(false);

  useEffect(() => setDraft(profile), [profile]);
  const initials = useMemo(() => draft.name.trim().split(/\s+/).slice(0, 2).map((part) => part[0]).join("").toUpperCase() || "G", [draft.name]);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const next = { ...draft, name: draft.name.trim() || "Knowledge explorer", email: draft.email.trim(), description: draft.description.trim() };
    onSave(next);
    setSaved(true);
    window.setTimeout(() => setSaved(false), 1600);
  };

  return (
    <div className="page-stack account-page">
      <section className="page-intro">
        <div><span className="eyebrow">Local identity</span><h1>Your profile</h1></div>
        <p>This profile personalizes the desktop only. It stays on this device.</p>
      </section>

      <section className="profile-layout">
        <aside className="panel profile-summary">
          <span className="profile-avatar">{initials}</span>
          <strong>{draft.name || "Knowledge explorer"}</strong>
          <small>{draft.email || "Local profile"}</small>
          <p>{draft.description || "Your personal knowledge workspace."}</p>
          <dl>
            <div><dt><Database size={14} />Sources</dt><dd>{overview.counts.sources}</dd></div>
            <div><dt><CircleUserRound size={14} />Entities</dt><dd>{overview.counts.entities}</dd></div>
            <div><dt><Network size={14} />Associations</dt><dd>{overview.counts.assertions}</dd></div>
          </dl>
        </aside>

        <form className="panel profile-form" onSubmit={submit}>
          <header className="panel-header">
            <div><span className="eyebrow">Profile details</span><h2>How Gunther addresses you</h2></div>
            {saved && <span className="profile-saved"><Check size={13} />Saved locally</span>}
          </header>
          <div className="profile-fields">
            <label><span>Display name</span><div><CircleUserRound size={15} /><input value={draft.name} onChange={(event) => setDraft({ ...draft, name: event.target.value })} placeholder="Knowledge explorer" /></div></label>
            <label><span>Email <small>optional</small></span><div><AtSign size={15} /><input type="email" value={draft.email} onChange={(event) => setDraft({ ...draft, email: event.target.value })} placeholder="you@example.com" /></div></label>
            <label><span>About your learning</span><textarea rows={5} value={draft.description} onChange={(event) => setDraft({ ...draft, description: event.target.value })} placeholder="What are you building knowledge about?" /></label>
          </div>
          <footer className="profile-form-footer">
            <span>Stored in this desktop’s local preferences.</span>
            <button className="button button-primary"><Save size={14} />Save profile</button>
          </footer>
        </form>
      </section>
    </div>
  );
}
