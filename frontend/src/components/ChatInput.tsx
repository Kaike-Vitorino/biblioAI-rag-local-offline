import { FormEvent, useState } from "react";

type ChatInputProps = {
  disabled?: boolean;
  onSend: (question: string) => Promise<void> | void;
};

export default function ChatInput({ disabled, onSend }: ChatInputProps) {
  const [value, setValue] = useState("");

  async function sendCurrentValue() {
    const question = value.trim();
    if (!question || disabled) return;
    setValue("");
    await onSend(question);
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await sendCurrentValue();
  }

  return (
    <form className="chat-input-form" onSubmit={handleSubmit}>
      <textarea
        className="chat-textarea"
        rows={3}
        value={value}
        onChange={(event) => setValue(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            void sendCurrentValue();
          }
        }}
        placeholder="Pergunte algo sobre os documentos..."
        disabled={disabled}
      />
      <button className="send-button" type="submit" disabled={disabled || !value.trim()}>
        {disabled ? "Consultando..." : "Enviar"}
      </button>
    </form>
  );
}
