export type JobFlow = "market" | "competitive";

export type JobStatus = "queued" | "running" | "completed" | "failed";

export type RevenueBasis = "bought_past_month" | "bsr_heuristic" | "unknown";

export interface ListingOut {
  asin: string;
  title: string;
  price: number | null;
  currency: string;
  bsr_rank: number | null;
  bsr_category: string | null;
  product_category?: string | null;
  avg_rating: number | null;
  review_count: number | null;
  canonical_url: string | null;
  estimated_monthly_units: number | null;
  estimated_monthly_revenue: number | null; // Always INR
  previous_month_units: number | null;
  revenue_basis: RevenueBasis;
  unit_price_inr: number | null;
}

export interface SummaryOut {
  asin: string;
  product_title: string;
  final_summary: string;
  key_purchase_criteria: string[];
  why_buyers_like?: string | null;
  why_buyers_caution?: string | null;
}

export interface ReviewOut {
  asin: string;
  rating: number | null;
  title: string | null;
  body: string;
  review_date: string | null;
  has_customer_images: boolean;
  verified: boolean;
}

export interface YouTubeReviewVideoLink {
  url: string;
  title: string;
  channel: string;
  reason: string;
}

export interface YouTubeCompetitorMention {
  product_name: string;
  mention_count: number;
  examples: string[];
}

export interface YouTubeInsights {
  product_display_name?: string | null;
  youtube_search_query_used?: string | null;
  youtube_demand_score?: number | null;
  creator_coverage_score?: number | null;
  trend_freshness_score?: number | null;
  top_questions: string[];
  competitor_mentions: YouTubeCompetitorMention[];
  review_video_links: YouTubeReviewVideoLink[];
  note?: string | null;
  error?: string | null;
}

export interface JobDetailResponse {
  id: string;
  flow: JobFlow;
  status: JobStatus;
  phase: string;
  error_message: string | null;
  /** Normalized Amazon host used for this job (e.g. amazon.de). */
  amazon_domain: string;
  bestsellers_url: string | null;
  product_url: string | null;
  competitor_urls: string[];
  asins: string[];
  market_totals_note: string | null;
  listings: ListingOut[];
  summaries: SummaryOut[];
  reviews?: ReviewOut[];
  reviews_count_total: number;
  created_at: string;
  ingest_demo: boolean;
  claude_configured: boolean;
  youtube_configured?: boolean;
  youtube_insights?: YouTubeInsights | null;
}

export interface BootstrapResponse {
  scraping_provider: string;
  claude_configured: boolean;
  youtube_configured?: boolean;
}
