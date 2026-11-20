async () => {
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const num = s => s ? Number(String(s).replace(/[^\d]/g, '')) : null;

  if (/\/sorry\//.test(location.href)) return { error: 'captcha', url: location.href };
  if (!/\/maps\/place\//.test(location.href)) return {
    error: 'not_a_place_page', url: location.href,
    candidates: [...document.querySelectorAll('a.hfpxzc')].slice(0, 10)
      .map(a => ({ name: a.getAttribute('aria-label'), url: a.href })) };

  const tab = document.querySelector('button[role=tab][aria-label^="Reviews"]');
  if (tab && tab.getAttribute('aria-selected') !== 'true') { tab.click(); await sleep(2500); }

  const hist = {};
  for (const img of document.querySelectorAll('[aria-label*="stars,"]')) {
    const m = img.getAttribute('aria-label').match(/^(\d) stars?, ([\d,.\s]+) reviews/);
    if (m) hist[m[1]] = num(m[2]);
  }

  const cards = [...document.querySelectorAll('[data-review-id].jftiEf')];
  cards.forEach(c => c.querySelectorAll('button.w8nwRe, button[aria-label="See more"]').forEach(b => b.click()));
  await sleep(800);

  const reviews = cards.map(c => {
    const owner = [...c.querySelectorAll('div')].find(d => /^Response from the owner/.test(d.innerText || ''));
    return {
      author: c.querySelector('.d4r55')?.innerText?.trim() || null,
      stars: num(c.querySelector('.kvMYJc')?.getAttribute('aria-label')?.match(/\d/)?.[0]),
      date: c.querySelector('.rsqaWe')?.innerText?.trim() || null,
      text: c.querySelector('.wiI7pd')?.innerText?.trim() || '',
      likes: num(c.querySelector('button[aria-label$="like"], button[aria-label$="likes"]')?.getAttribute('aria-label')) || 0,
      owner_response: owner ? owner.innerText.replace(/^Response from the owner\s*/, '').trim() : null,
    };
  });

  return {
    url: location.href.split('?')[0],
    place: document.querySelector('h1')?.innerText?.trim() || document.title.replace(/ - Google Maps$/, ''),
    rating: Number((document.querySelector('[role=img][aria-label$=" stars"]')?.getAttribute('aria-label') || '').match(/[\d.]+/)?.[0]) || null,
    total_reviews: Object.values(hist).reduce((a, b) => a + b, 0) || null,
    histogram: hist,
    topics: [...document.querySelectorAll('[role=radio]')]
      .map(r => (r.getAttribute('aria-label') || '').match(/^(.+), mentioned in ([\d,]+) reviews/))
      .filter(Boolean).map(m => ({ topic: m[1], count: num(m[2]) })),
    limited_view: /limited view of Google Maps/.test(document.body.innerText),
    reviews,
  };
}
