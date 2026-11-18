/**
 * GitHub repository URLs for the Model Info provenance links.
 *
 * The repository is baked in at build time because the frontend is a static
 * bundle: set `VITE_GITHUB_REPO` (Docker Compose passes it as a build arg) to
 * point a fork at its own sources. Without it, the canonical repository is
 * used, which also covers `npm run dev`.
 */

export const DEFAULT_REPO = 'https://github.com/mayolito-waje/smokeguard';
export const GITHUB_BRANCH = 'main';

function normalizeRepo(raw: string | undefined): string {
  const value = raw?.trim();
  if (!value) return DEFAULT_REPO;
  const url = /^[^/]+\/[^/]+$/.test(value)
    ? `https://github.com/${value}`
    : value;
  return url.replace(/\.git$/, '').replace(/\/+$/, '');
}

export const GITHUB_REPO = normalizeRepo(import.meta.env.VITE_GITHUB_REPO);

export function githubBlobUrl(
  path: string,
  ref: string = GITHUB_BRANCH,
): string {
  return `${GITHUB_REPO}/blob/${ref}/${path.replace(/^\/+/, '')}`;
}
