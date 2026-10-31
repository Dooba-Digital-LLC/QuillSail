// Opt-in Cloudflare module Worker. No logging of subscriber addresses or tokens.
const headers = {"Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store",
  "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'",
  "Referrer-Policy": "no-referrer", "X-Content-Type-Options": "nosniff"};
const reply = (body, status = 200) => new Response(body, {status, headers});
const page = message => `<!doctype html><html lang="en"><meta name="viewport" content="width=device-width"><title>Publication offline</title><main><h1>We'll be back shortly</h1><p>${message}</p></main></html>`;
async function limitedBody(request) {
  const reader = request.body?.getReader();
  if (!reader) return "";
  let total = 0, chunks = [];
  try {
    while (true) {
      const {done, value} = await reader.read();
      if (done) break;
      total += value.byteLength;
      if (total > 4096) throw new Error("Too large");
      chunks.push(value);
    }
  } finally { reader.releaseLock(); }
  let content = new Uint8Array(total), offset = 0;
  for (const chunk of chunks) { content.set(chunk, offset); offset += chunk.byteLength; }
  return new TextDecoder().decode(content);
}
export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.hostname !== env.HOST) return reply("Not found", 404);
    try {
      const upstream = await fetch(request.clone());
      if (![520, 521, 522, 523, 524, 525, 526, 527, 530].includes(upstream.status)) return upstream;
    } catch { /* Origin unreachable: offer only the minimal public fallback. */ }
    if (request.method === "GET" && url.pathname === "/") {
      const snapshot = await env.QUEUE.get("snapshot");
      return reply(snapshot || page("This publication's computer is offline. Please visit again soon."), snapshot ? 200 : 503);
    }
    const tokenMatch = /^\/(unsubscribe|subscription)\/([A-Za-z0-9_-]{20,64})$/.exec(url.pathname);
    if (request.method === "GET" && tokenMatch) {
      return reply(page('You can still unsubscribe while the publication is offline.') +
        `<form method="post"><input type="hidden" name="action" value="unsubscribe"><button>Unsubscribe</button></form>`);
    }
    if (request.method !== "POST" || !(url.pathname === "/subscribe" || tokenMatch))
      return reply(page("This publication's computer is offline. Please visit again soon."), 503);
    if (!(request.headers.get("Content-Type") || "").startsWith("application/x-www-form-urlencoded"))
      return reply("Use a form submission.", 415);
    // The platform rate-limit binding is independent of eventually consistent KV.
    const ip = request.headers.get("CF-Connecting-IP");
    if (!ip || !(await env.LIMITER.limit({key: ip})).success) return reply("Please try again later.", 429);
    let form;
    try { form = new URLSearchParams(await limitedBody(request)); }
    catch { return reply("The form is too large.", 413); }
    if (tokenMatch && tokenMatch[1] === "subscription" && form.get("action") !== "unsubscribe")
      return reply(page("Subscription preferences can be changed when the publication is back online."), 503);
    let entry = {received: new Date().toISOString(), action: tokenMatch ? "unsubscribe" : "subscribe"};
    if (tokenMatch) entry.token = tokenMatch[2];
    else {
      if (form.get("website")) return reply(page("Your confirmation email will arrive shortly."));
      const email = (form.get("email") || "").trim().toLowerCase();
      if (email.length > 254 || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) return reply("Enter a valid email address.", 422);
      entry.email = email;
      entry.name = (form.get("name") || "").slice(0, 100);
    }
    try {
      // No expiration: a queued unsubscribe must survive extended downtime.
      await env.QUEUE.put("action:" + crypto.randomUUID(), JSON.stringify(entry));
    } catch { return reply(page("Your request could not be saved. Please try again later."), 503); }
    return reply(page(tokenMatch ? "You're unsubscribed." : "Your confirmation email will arrive shortly."));
  }
};
