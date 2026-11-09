import type { Settings, SiteRule } from "./types";

export function siteRuleForHost(rules: SiteRule[], host: string): SiteRule | null {
  const normalized = host.toLowerCase();
  let wildcard: SiteRule | null = null;
  for (const rule of rules) {
    if (rule.enabled === false) continue;
    const pattern = rule.host.toLowerCase();
    if (pattern === normalized) return rule;
    if (pattern.startsWith("*.") && normalized.endsWith(pattern.slice(1))) wildcard = rule;
  }
  return wildcard;
}

export function effectiveSettings(settings: Settings, host: string): Settings {
  const rule = siteRuleForHost(settings.site_rules, host);
  if (!rule) return settings;
  return {
    ...settings,
    target_lang: rule.target_lang ?? settings.target_lang,
    source_lang: rule.source_lang ?? settings.source_lang,
    form: rule.form ?? settings.form,
    glossary: rule.glossary ?? settings.glossary,
  };
}

export function hostShouldAutoTranslate(settings: Settings, host: string): boolean {
  return siteRuleForHost(settings.site_rules, host)?.auto_translate === true;
}
