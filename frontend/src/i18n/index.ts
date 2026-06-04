import ko from "./ko.json";

export type I18nKey = keyof typeof ko;

const dict: Record<string, string> = ko;

/**
 * Translate a key to its Korean string. Supports `{name}` interpolation.
 * Unknown keys fall back to the key itself (visible during dev, never throws).
 */
export function translate(
  key: I18nKey | string,
  vars?: Record<string, string | number>,
): string {
  let str = dict[key as string];
  if (str === undefined) return String(key);
  if (vars) {
    for (const [name, value] of Object.entries(vars)) {
      str = str.replace(new RegExp(`\\{${name}\\}`, "g"), String(value));
    }
  }
  return str;
}

/**
 * Hook form. The dictionary is static (single locale) so this just returns the
 * stable `translate` function — kept as a hook for a consistent call site and
 * easy future locale switching.
 */
export function useT(): typeof translate {
  return translate;
}
