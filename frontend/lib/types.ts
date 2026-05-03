export type JobFlow = "market" | "competitive";

export type JobStatus = "queued" | "running" | "completed" | "failed";

export interface ListingOut {
  asin: string;
  title: string;
  price: number | null;
  currency: string;
  bsr_rank: number | null;
  bsr_category: string | null;
  avg_rating: number | null;
  review_count: number | null;
  canonical_url: string | null;
  estimated_monthly_units: number | null;
  estimated_monthly_revenue: number | null;
}

export interface SummaryOut {
  asin: string;
  final_summary: string;
  key_purchase_criteria: string[];
}

export interface JobDetailResponse {
  id: string;
  flow: JobFlow;
  status: JobStatus;
  phase: string;
  error_message: string | null;
  bestsellers_url: string | null;
  product_url: string | null;
  competitor_urls: string[];
  asins: string[];
  market_totals_note: string | null;
  listings: ListingOut[];
  summaries: SummaryOut[];
  reviews_count_total: number;
  created_at: string;
}
