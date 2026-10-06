export type Doc = {
  id: string;
  status: string;
  seller_name?: string;
  buyer_name?: string;
  created_at: string | number;
  posted_reference?: string;
  fiscal_verification?: string;
  provenance?: "buyer_scan" | "seller_submission";
  files?: {id: string; filename: string; kind: string; media_type: string; sha256: string; retain_until: number; erp_status?: string}[];
  fiscal_check?: {status: string; provider?: string; evidence_reference?: string; checked_at?: number};
  payload: {
    document_type: string;
    external_reference: string;
    currency: string;
    issued_at: string;
    fiscal_metadata?: {verification_url?: string; receipt_reference?: string} | null;
    seller: { cbin_id: string; tin: string };
    buyer: { cbin_id: string; tin: string };
    line_items: {
      item_code: string;
      description: string;
      quantity: string;
      unit_price_minor: number;
      tax_rate: string;
    }[];
    totals: {
      subtotal_minor: number;
      tax_minor: number;
      grand_total_minor: number;
    };
  };
  timeline?: AuditEvent[];
};
export type AuditEvent = {
  id: number;
  kind: string;
  created_at: string | number;
  data?: unknown;
  document_id?: string;
};
