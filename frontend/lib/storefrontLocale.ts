/**
 * BCP 47 locale hints for Intl.NumberFormat, keyed by normalized Amazon host (no www), e.g. amazon.de.
 */
const AMAZON_HOST_TO_LOCALE: Record<string, string> = {
  "amazon.com": "en-US",
  "amazon.com.mx": "es-MX",
  "amazon.ca": "en-CA",
  "amazon.co.uk": "en-GB",
  "amazon.de": "de-DE",
  "amazon.fr": "fr-FR",
  "amazon.it": "it-IT",
  "amazon.es": "es-ES",
  "amazon.in": "en-IN",
  "amazon.co.jp": "ja-JP",
  "amazon.com.au": "en-AU",
  "amazon.com.br": "pt-BR",
  "amazon.nl": "nl-NL",
  "amazon.se": "sv-SE",
  "amazon.pl": "pl-PL",
  "amazon.sg": "en-SG",
  "amazon.ae": "en-AE",
};

export function localeForAmazonDomain(host: string | null | undefined): string {
  const h = (host || "").trim().toLowerCase().replace(/^www\./, "");
  if (!h) {
    return "en-US";
  }
  return AMAZON_HOST_TO_LOCALE[h] || "en-US";
}

export function formatStorefrontMoney(
  amount: number,
  currencyCode: string,
  locale: string,
  maximumFractionDigits = 2,
): string {
  const cur = (currencyCode || "USD").trim().toUpperCase();
  try {
    return new Intl.NumberFormat(locale, {
      style: "currency",
      currency: cur,
      maximumFractionDigits,
    }).format(amount);
  } catch {
    return `${cur} ${amount.toFixed(maximumFractionDigits)}`;
  }
}
