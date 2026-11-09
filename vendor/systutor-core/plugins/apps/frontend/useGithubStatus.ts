import { useQuery } from "@tanstack/react-query";

import { appsKeys, getGithubStatus } from "./api";

/** Single readiness query owner for the Apps page. */
export function useGithubStatus(enabled: boolean) {
  return useQuery({
    queryKey: appsKeys.status,
    queryFn: getGithubStatus,
    enabled,
    retry: false,
    staleTime: 5_000,
  });
}
