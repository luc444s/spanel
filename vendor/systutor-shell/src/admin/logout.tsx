import { clearToken } from "../auth/token";
import { Button } from "../ui/button";

type LogoutButtonProps = {
  onLogout: () => void;
  label?: string;
};

export function LogoutButton({ onLogout, label = "Cerrar sesión" }: LogoutButtonProps) {
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
