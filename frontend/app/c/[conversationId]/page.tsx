import { redirect } from "next/navigation";

// Preserves any previously-bookmarked/shared "/c/{id}" links from before the
// Anonymization app moved under /apps/anonymization.
export default async function LegacyConversationRedirect({
  params,
}: {
  params: Promise<{ conversationId: string }>;
}) {
  const { conversationId } = await params;
  redirect(`/apps/anonymization/c/${conversationId}`);
}
