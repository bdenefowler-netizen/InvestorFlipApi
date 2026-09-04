import { Tabs } from "expo-router";
import { Ionicons } from "@expo/vector-icons";
import { colors } from "@/src/theme/tokens";

export default function TabsLayout() {
  return (
    <Tabs
      screenOptions={{
        headerShown: false,
        tabBarActiveTintColor: colors.brandPrimary,
        tabBarInactiveTintColor: colors.muted,
        tabBarStyle: {
          backgroundColor: colors.surfaceSecondary,
          borderTopColor: colors.border,
          height: 64,
          paddingTop: 6,
          paddingBottom: 8,
        },
        tabBarLabelStyle: { fontSize: 11, fontWeight: "700" },
      }}
    >
      <Tabs.Screen
        name="index"
        options={{
          title: "Listings",
          tabBarIcon: ({ color, size }) => <Ionicons name="home" size={size} color={color} />,
        }}
      />
      <Tabs.Screen
        name="saved"
        options={{
          title: "Saved",
          tabBarIcon: ({ color, size }) => <Ionicons name="bookmark" size={size} color={color} />,
        }}
      />
      <Tabs.Screen
        name="county"
        options={{
          title: "County",
          tabBarIcon: ({ color, size }) => <Ionicons name="grid" size={size} color={color} />,
        }}
      />
      <Tabs.Screen
        name="add"
        options={{
          title: "Add",
          tabBarIcon: ({ color, size }) => <Ionicons name="add-circle" size={size} color={color} />,
        }}
      />
      <Tabs.Screen
        name="calculate"
        options={{
          title: "Calculator",
          tabBarIcon: ({ color, size }) => <Ionicons name="calculator" size={size} color={color} />,
        }}
      />
      <Tabs.Screen
        name="settings"
        options={{
          title: "Settings",
          tabBarIcon: ({ color, size }) => <Ionicons name="settings" size={size} color={color} />,
        }}
      />
    </Tabs>
  );
}
