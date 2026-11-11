import { clearToken } from "../auth/token";
import { Button } from "../ui/button";

type LogoutButtonProps = {
  onLogout: () => void;
  label?: string;
};

export function LogoutButton({ onLogout, label = "Sign out" }: LogoutButtonProps) {
  return (
    <Button
      variant="secondary"
      onClick={() => {
        clearToken();
        onLogout();
      }}
    >
      {label}
    </Button>
  );
}
