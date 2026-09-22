import { useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";

import { clearClientSession } from "./session";

export function useLogoutAction() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  return () => {
    clearClientSession(queryClient);
    navigate("/login", { replace: true });
  };
}
