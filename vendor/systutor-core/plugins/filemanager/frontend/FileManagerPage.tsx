import { useNavigate, useParams } from "react-router-dom";

import { FileExplorer } from "./FileExplorer";

export default function FileManagerPage() {
  const { provider = "", resourceId = "" } = useParams();
  const navigate = useNavigate();

  return (
    <FileExplorer
      provider={provider}
      resourceId={resourceId}
      onClose={() => navigate("/app/hosting")}
    />
  );
}
