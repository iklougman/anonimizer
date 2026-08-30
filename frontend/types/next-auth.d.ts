import "next-auth";
import "next-auth/jwt";

export type AuthTokenError = "RefreshAccessTokenError";

declare module "next-auth" {
  interface Session {
    accessToken: string;
    error?: AuthTokenError;
  }
}

declare module "next-auth/jwt" {
  interface JWT {
    accessToken?: string;
    refreshToken?: string;
    expiresAt?: number;
    error?: AuthTokenError;
  }
}
