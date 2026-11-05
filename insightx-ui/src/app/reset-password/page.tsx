import type { Metadata } from "next";
import { Suspense } from "react";
import { ResetPasswordScreen } from "@/components/auth/password-reset-screens";

// The reset token is in the address: never send it to other sites.
export const metadata: Metadata = { title: "Choose a new password", referrer: "no-referrer" };

export default function ResetPasswordPage() {
  return (
    <Suspense fallback={null}>
      <ResetPasswordScreen />
    </Suspense>
  );
}
