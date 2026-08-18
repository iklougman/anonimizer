import type { ConversationDetail, ConversationSummary, MessageOut } from "./types";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export async function listConversations(accessToken: string): Promise<ConversationSummary[]> {
  const response = await fetch(`${API_BASE_URL}/api/conversations`, {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  if (!response.ok) throw new Error(`failed to list conversations: ${response.status}`);
  return response.json();
}

export async function createConversation(accessToken: string): Promise<ConversationSummary> {
  const response = await fetch(`${API_BASE_URL}/api/conversations`, {
    method: "POST",
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  if (!response.ok) throw new Error(`failed to create conversation: ${response.status}`);
  return response.json();
}

export async function deleteConversation(accessToken: string, conversationId: string): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/api/conversations/${conversationId}`, {
    method: "DELETE",
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  if (!response.ok && response.status !== 204) {
    throw new Error(`failed to delete conversation: ${response.status}`);
  }
}

export async function getConversation(
  accessToken: string,
  conversationId: string
): Promise<ConversationDetail> {
  const response = await fetch(`${API_BASE_URL}/api/conversations/${conversationId}`, {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  if (!response.ok) throw new Error(`failed to load conversation: ${response.status}`);
  return response.json();
}

export async function getMessages(accessToken: string, conversationId: string): Promise<MessageOut[]> {
  const response = await fetch(`${API_BASE_URL}/api/conversations/${conversationId}/messages`, {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  if (!response.ok) throw new Error(`failed to load messages: ${response.status}`);
  return response.json();
}
