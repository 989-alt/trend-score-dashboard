import { useT } from "../i18n";
import type { MergedTheme, ScoreEntry } from "../types";
import { ThemeCard } from "./ThemeCard";
import styles from "./ThemeBoard.module.css";

interface Props {
  themes: MergedTheme[];
  onSelect: (entry: ScoreEntry) => void;
}

export function ThemeBoard({ themes, onSelect }: Props) {
  const t = useT();
  if (themes.length === 0) {
    return <div className={styles.empty}>{t("theme.board.empty")}</div>;
  }
  return (
    <div className={styles.grid}>
      {themes.map((theme) => (
        <ThemeCard key={theme.theme} theme={theme} onSelect={onSelect} />
      ))}
    </div>
  );
}
