/**
 * Hook — fetch smoking-related facts from Wikipedia (cached in localStorage),
 * falling back to baked-in facts when the network is unavailable.
 */

import { useState, useEffect } from 'react';

// ---------------------------------------------------------------------------
// Wikipedia pages whose intro paragraphs we scrape for sentences
// ---------------------------------------------------------------------------

const WIKI_PAGES = [
  'Tobacco_smoking',
  'Cigarette',
  'Health_effects_of_tobacco',
  'Smoking_cessation',
  'Nicotine',
  'Electronic_cigarette',
  'Passive_smoking',
  'Tobacco_control',
  'History_of_smoking',
  'Prevalence_of_tobacco_use',
  'Tobacco_advertising',
  'Smoking_bans',
  'Tar_(tobacco_residue)',
  'Tobacco_harm_reduction',
  'List_of_smoking_bans',
];

const WIKI_API = 'https://en.wikipedia.org/w/api.php';

const CACHE_KEY = 'smokeguard-wiki-facts';
const CACHE_TTL_MS = 24 * 60 * 60 * 1000; // 24 hours

// ---------------------------------------------------------------------------
// Hardcoded fallback facts (used when offline / API fails)
// ---------------------------------------------------------------------------

const FALLBACK: string[] = [
  'Cigarette smoke contains over 7,000 chemicals — at least 70 are known to cause cancer.',
  'Every year, tobacco kills more than 8 million people worldwide.',
  'Second-hand smoke causes ~1.2 million deaths annually among non-smokers.',
  'Within 20 minutes of quitting, your heart rate & blood pressure begin to drop.',
  'Quitting before age 40 reduces smoking-related death risk by ~90%.',
  'Approximately 4.5 trillion cigarette butts are littered each year — the #1 littered item on Earth.',
  'Nicotine reaches the brain within 10–20 seconds of inhaling cigarette smoke.',
  'Smoking increases heart disease risk by 2–4× versus non-smokers.',
  'E-cigarettes heat nicotine without combustion, but long-term effects are still under study.',
  'Tobacco farming occupies ~3.5 million hectares of cropland globally.',
  'Over 70 countries have adopted comprehensive smoke-free laws in public places.',
  'The WHO Framework Convention on Tobacco Control is the first-ever global public health treaty.',
  'China has the world\'s largest smoking population: over 300 million smokers.',
  'Tobacco use fell from ~33% of adults in 2000 to ~20% in 2022 — but the work continues.',
  'Smoking damages nearly every organ in the human body.',
  'Cigarette butts can take up to 10 years to decompose in the environment.',
  'In 2023, roughly 11.5% of U.S. adults were cigarette smokers.',
  'The tobacco industry spends billions annually on marketing — much of it targeting youth.',
  'About 80% of the world\'s 1.3 billion tobacco users live in low- & middle-income countries.',
  'Tobacco smoke contains radioactive polonium-210, which accumulates in lungs over time.',
  'Smokers are 15–30× more likely to develop lung cancer than non-smokers.',
  'The first smoking ban in a public building was enacted in 1590 by Pope Urban VII.',
  'Cigarette filters were introduced in the 1950s but do not reduce health risks.',
  'A single cigarette reduces life expectancy by approximately 11 minutes on average.',
  'Smoking rates among U.S. high school students dropped from 36% in 1997 to ~2% in 2024.',
  'The global economic cost of smoking is estimated at over $1.4 trillion per year.',
  'Tobacco was first cultivated in the Americas around 6,000 BCE.',
  'Menthol cigarettes are disproportionately marketed to African American communities.',
  'Third-hand smoke — residue on surfaces — can persist for months after smoking stops.',
  'The WHO estimates that 65,000 children die each year from second-hand smoke exposure.',
];

// ---------------------------------------------------------------------------
// Cache helpers
// ---------------------------------------------------------------------------

interface CacheEntry {
  ts: number;       // Date.now() when cached
  facts: string[];
}

function readCache(): CacheEntry | null {
  try {
    const raw = localStorage.getItem(CACHE_KEY);
    if (!raw) return null;
    const entry: CacheEntry = JSON.parse(raw);
    if (Date.now() - entry.ts > CACHE_TTL_MS) {
      localStorage.removeItem(CACHE_KEY);
      return null;
    }
    if (!Array.isArray(entry.facts) || entry.facts.length === 0) return null;
    return entry;
  } catch {
    return null;
  }
}

function writeCache(facts: string[]): void {
  try {
    localStorage.setItem(CACHE_KEY, JSON.stringify({ ts: Date.now(), facts }));
  } catch { /* quota exceeded — ignore */ }
}

// ---------------------------------------------------------------------------
// Wikipedia fetch
// ---------------------------------------------------------------------------

async function fetchWikiFacts(): Promise<string[]> {
  const params = new URLSearchParams({
    action: 'query',
    format: 'json',
    origin: '*',
    prop: 'extracts',
    exintro: '1',
    explaintext: '1',
    titles: WIKI_PAGES.join('|'),
  });

  const res = await fetch(`${WIKI_API}?${params}`);
  if (!res.ok) throw new Error(`Wikipedia returned ${res.status}`);

  const json = await res.json();
  const pages: Record<string, { extract?: string }> = json?.query?.pages ?? {};

  const sentences: string[] = [];
  for (const page of Object.values(pages)) {
    const text = page?.extract;
    if (!text) continue;
    // Split into sentences, keep only substantive ones
    const parts = text
      .split(/(?<=[.!?])\s+/)
      .map(s => s.trim().replace(/\s+/g, ' '))
      .filter(s => {
        if (s.length < 40) return false;           // too short
        if (s.length > 320) return false;          // too long
        if (/^\[|may refer to|refers to/i.test(s)) return false; // disambig
        return true;
      });
    sentences.push(...parts);
  }

  if (sentences.length === 0) throw new Error('No sentences extracted');
  return shuffle(sentences);
}

// ---------------------------------------------------------------------------
// Fisher–Yates shuffle
// ---------------------------------------------------------------------------

function shuffle<T>(arr: T[]): T[] {
  const a = [...arr];
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export function useSmokingFacts(): string[] {
  const [facts, setFacts] = useState<string[]>(() => {
    const cached = readCache();
    return cached ? cached.facts : FALLBACK;
  });

  useEffect(() => {
    // Don't re-fetch if we already have cached facts
    if (readCache()) return;

    let cancelled = false;
    fetchWikiFacts()
      .then(wikiFacts => {
        if (cancelled) return;
        const combined = shuffle([...wikiFacts, ...FALLBACK]);
        writeCache(combined);
        setFacts(combined);
      })
      .catch(() => {
        // Keep fallback — already set in useState initializer
      });

    return () => { cancelled = true; };
  }, []);

  return facts;
}
