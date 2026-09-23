/**
 * Converts the `next` value from the login URL into a safe, internal target.
 * Only relative application paths are accepted, preventing open redirects.
 */
export function returnToPath(next: string | null): string {
  if (!next || !next.startsWith("/") || next.startsWith("//") || next.startsWith("/\\")) {
    return "/";
  }

  const base = "http://return-to.local";
  const destination = new URL(next, base);
  if (
    destination.origin !== base
    || destination.pathname === "/login"
    || destination.pathname.startsWith("/login/")
  ) {
    return "/";
  }

  return `${destination.pathname}${destination.search}${destination.hash}`;
}
