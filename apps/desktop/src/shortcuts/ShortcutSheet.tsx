import { Keyboard, X } from "lucide-react";
import { useEffect, useRef } from "react";
import { comboKeys, SHORTCUTS, useEscape, type ShortcutGroup } from "./shortcuts";

const GROUPS: ShortcutGroup[] = ["General", "Go to", "Capture", "Inbox and items", "Writing", "Recordings"];

function Combo({ combo }: { combo: string }) {
  return <span className="gx-combo">{comboKeys(combo).map((key, index) => <kbd key={`${key}-${index}`}>{key}</kbd>)}</span>;
}

/** Every shortcut in one calm sheet, generated from the same registry that handles them. */
export function ShortcutSheet({ open, onClose }: { open: boolean; onClose: () => void }) {
  const closeButton = useRef<HTMLButtonElement>(null);
  useEscape(onClose, open);
  useEffect(() => {
    if (!open) return undefined;
    const previous = document.activeElement as HTMLElement | null;
    closeButton.current?.focus();
    return () => previous?.focus?.();
  }, [open]);
  if (!open) return null;
  return (
    <div className="gx-sheet-scrim" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <section className="gx-shortcut-sheet" role="dialog" aria-modal="true" aria-labelledby="gx-shortcuts-title">
        <header>
          <span className="gx-shortcut-icon"><Keyboard size={16} /></span>
          <h2 id="gx-shortcuts-title">Keyboard shortcuts</h2>
          <button ref={closeButton} type="button" className="gx-icon-button" onClick={onClose} aria-label="Close" title="Close  Esc"><X size={15} /></button>
        </header>
        <div className="gx-shortcut-groups">
          {GROUPS.map((group) => (
            <section key={group} aria-label={group}>
              <h3>{group}</h3>
              <dl>
                {SHORTCUTS.filter((shortcut) => shortcut.group === group).map((shortcut) => (
                  <div key={shortcut.id}>
                    <dt>{shortcut.label}</dt>
                    <dd>{shortcut.keys.map((combo, index) => <span key={combo}>{index > 0 && <em>or</em>}<Combo combo={combo} /></span>)}</dd>
                  </div>
                ))}
              </dl>
            </section>
          ))}
        </div>
        <footer>Single-letter shortcuts pause while you type. <Combo combo="escape" /> always closes the top-most panel first.</footer>
      </section>
    </div>
  );
}
