import { useMutation, useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { useSearchParams } from "react-router-dom";
import { api, unwrap } from "@/api/client";
import { ErrorBox } from "@/components/ErrorBox";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export function OAuthConsentPage() {
  const { t } = useTranslation();
  const [q] = useSearchParams();
  const clientId = q.get("client_id") ?? "";
  const client = useQuery({
    queryKey: ["oauth-client", clientId],
    queryFn: async () => unwrap(await api.GET("/api/v1/oauth/clients/{client_id}", { params: { path: { client_id: clientId } } })),
    enabled: !!clientId,
  });
  const allow = useMutation({
    mutationFn: async () =>
      unwrap(
        await api.POST("/api/v1/oauth/authorize", {
          body: {
            client_id: clientId,
            redirect_uri: q.get("redirect_uri") ?? "",
            scope: q.get("scope") ?? "dpac.read",
            code_challenge: q.get("code_challenge") ?? "",
            code_challenge_method: q.get("code_challenge_method") ?? "S256",
            state: q.get("state"),
          },
        }),
      ),
    onSuccess: (r) => {
      window.location.href = r.redirect_url;
    },
  });
  return (
    <Card className="mx-auto max-w-md">
      <CardHeader>
        <CardTitle>
          <h1>{t("oauth.title")}</h1>
        </CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <p>{t("oauth.question", { client: client.data?.name ?? clientId, scope: q.get("scope") ?? "dpac.read" })}</p>
        <div className="flex gap-2">
          <Button onClick={() => allow.mutate()}>{t("oauth.allow")}</Button>
          <Button variant="outline" onClick={() => (window.location.href = "/jobs")}>
            {t("oauth.deny")}
          </Button>
        </div>
        <ErrorBox error={allow.error ?? client.error} />
      </CardContent>
    </Card>
  );
}
