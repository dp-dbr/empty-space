import { useQuery } from "@tanstack/react-query";
import { api, unwrap, type Schemas } from "@/api/client";

export type Me = Schemas["MeOut"];

export function useMe() {
  return useQuery({
    queryKey: ["me"],
    queryFn: async () => unwrap(await api.GET("/api/v1/auth/me")),
    retry: false,
  });
}

export function hasRole(me: Me | undefined, ...roles: string[]) {
  return !!me && me.user.roles.some((r) => roles.includes(r));
}
