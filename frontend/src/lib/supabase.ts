/** Shared Supabase client for auth. The URL + anon key are public credentials. */

import { createClient } from "@supabase/supabase-js";

const SUPABASE_URL =
  import.meta.env.VITE_SUPABASE_URL ?? "https://ftdmbprinolyglvfbevl.supabase.co";
const SUPABASE_ANON_KEY =
  import.meta.env.VITE_SUPABASE_ANON_KEY ??
  "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImZ0ZG1icHJpbm9seWdsdmZiZXZsIiwicm9sZSI6ImFub24iLCJpYXQiOjE3OTM5Mzg1NjcsImV4cCI6MjEwOTUxNDU2N30.9-vz5fHjZB3gh6RW1iKZ7WHFwMDvNSv3LWiIzAq0QhI";

export const supabase = createClient(SUPABASE_URL, SUPABASE_ANON_KEY);
