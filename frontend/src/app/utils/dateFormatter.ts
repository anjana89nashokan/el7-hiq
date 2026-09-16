import { MESSAGES } from '../config/messages';

export const formatDate = (dateString: string): string => {
  if (!dateString) return MESSAGES.DEFAULTS.NA;
  try {
    const date = new Date(dateString + 'Z');
    return date.toLocaleString('en-US', {
      timeZone: 'America/New_York',
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: true
    });
  } catch {
    return dateString;
  }
};

export const formatToLocalTime = (utcString: string): string => {
  if (!utcString) return "";

  const normalizedString = utcString.endsWith("Z") ? utcString : `${utcString}Z`;
  
  const date = new Date(normalizedString);

  return new Intl.DateTimeFormat(navigator.language, {
    hour: "numeric",
    minute: "2-digit",
    hour12: true, // Set to false if need 24h format
  }).format(date);
};


export const formatToLocalDateTime = (utcString: string): string => {
  if (!utcString) return "";
  
  const normalizedString = utcString.endsWith("Z") ? utcString : `${utcString}Z`;
  const date = new Date(normalizedString);

  return new Intl.DateTimeFormat(navigator.language, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
  }).format(date);
};