export type JudgeId = "offline" | "claude";
export type JudgeOption = { id: JudgeId; name: string; detail: string; available: boolean; reason?: string | null };

export const DEFAULT_JUDGES: JudgeOption[] = [
  { id: "offline", name: "Offline judge", detail: "Runs OWLv2 on this computer and checks which objects moved in the video. No Anthropic credits.", available: true },
  { id: "claude", name: "Claude judge", detail: "Claude reviews the video and decides whether the task finished. Uses Anthropic credits.", available: true },
];

export function JudgePicker({ judge, judges, onChange, disabled }: { judge: JudgeId; judges: JudgeOption[]; onChange: (judge: JudgeId) => void; disabled?: boolean }) {
  const current = judges.find((item) => item.id === judge);
  return (
    <div className="judge-picker">
      <label>
        Judge
        <select aria-label="Judge" value={judge} disabled={disabled} onChange={(event) => onChange(event.target.value as JudgeId)}>
          {judges.map((item) => (
            <option key={item.id} value={item.id} disabled={!item.available}>
              {item.name}{item.available ? "" : " (not available)"}
            </option>
          ))}
        </select>
      </label>
      <p className="hint">{current?.available === false ? current.reason : current?.detail}</p>
    </div>
  );
}
