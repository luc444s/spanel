import { apiRequest } from "@systutor/shell/api/client";

export type MailAccountsResponse = {
  domain: string;
  accounts: string[];
};

export type MailMessageResponse = {
  message: string;
  email: string | null;
};

export type CreateAccountPayload = {
  username: string;
  password: string;
};

export type ChangePasswordPayload = {
  password: string;
};

export const mailKeys = {
  accounts: ["mail", "accounts"] as const,
};

export function listMailAccounts() {
  return apiRequest<MailAccountsResponse>("/api/v1/plugins/mail/mail/accounts");
}

export function createMailAccount(payload: CreateAccountPayload) {
  return apiRequest<MailMessageResponse>("/api/v1/plugins/mail/mail/accounts", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function changeMailAccountPassword(email: string, payload: ChangePasswordPayload) {
  return apiRequest<MailMessageResponse>(
    `/api/v1/plugins/mail/mail/accounts/${encodeURIComponent(email)}/password`,
    {
      method: "PUT",
      body: JSON.stringify(payload),
    }
  );
}
